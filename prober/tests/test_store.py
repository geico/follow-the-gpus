import re
import sys
import threading
import time
import types
import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest

from ftg_prober.awsclient import boto3_client
from ftg_prober.store import (
    BlobStore,
    FileStore,
    S3Store,
    build_key,
    logs_key,
    open_store,
    raw_key,
    slug,
    target_for,
)
from tests.test_record import make_record

KEY_RE = re.compile(
    r"^experiment=[a-z0-9-]+/provider=(aws|azure)/probe_type=[a-z_]+/date=\d{4}-\d{2}-\d{2}/"
    r"\d{8}T\d{6}Z_[a-z0-9-]+_[0-9a-f-]{36}\.jsonl$"
)
MOMENT = datetime(2026, 10, 11, 23, 59, 58, 900_000, tzinfo=UTC)
UID = uuid.UUID(int=0x1234, version=4)  # the text form is not a literal here


def test_slug_is_lower_case_with_dashes_only():
    assert slug("p5.48xlarge") == "p5-48xlarge"
    assert slug("Standard_ND96isr_H100_v5") == "standard-nd96isr-h100-v5"
    assert slug("us-east-1") == "us-east-1"
    assert slug("__x__") == "x"
    assert slug("") == "none"


def test_target_for_one_target_and_for_many():
    one = [make_record(), make_record()]
    assert target_for(one) == "p5-48xlarge-us-east-1"
    many_skus = [make_record(), make_record(sku="g5.xlarge")]
    assert target_for(many_skus) == "multi-us-east-1"
    many_both = [make_record(), make_record(sku="g5.xlarge", region="us-west-2")]
    assert target_for(many_both) == "multi-multi"


def test_build_key_follows_spec_7_3():
    key = build_key("ftg", "aws", "aws_spot_score", MOMENT, "p5-48xlarge-us-east-1", UID)
    assert key == (
        "experiment=ftg/provider=aws/probe_type=aws_spot_score/date=2026-10-11/"
        f"20261011T235958Z_p5-48xlarge-us-east-1_{UID}.jsonl"
    )
    assert KEY_RE.match(key)


def test_build_key_uses_utc_for_the_date_partition():
    local = datetime(2026, 10, 12, 1, 30, tzinfo=UTC).astimezone(timezone(timedelta(hours=5)))
    key = build_key("ftg", "aws", "aws_spot_score", local, "x", UID)
    assert "/date=2026-10-12/20261012T013000Z_" in key


def test_build_key_makes_a_new_uuid_each_time():
    keys = {build_key("ftg", "aws", "aws_spot_score", MOMENT, "x") for _ in range(5)}
    assert len(keys) == 5


def test_build_key_has_no_simulated_parameter():
    """ADR 0011 section 6: `simulated` is a row field, not a path segment, so `build_key`
    takes no `simulated`/`run_mode`-shaped argument at all: nothing in its signature could
    make the key differ for a simulated row versus a real one."""
    import inspect

    assert set(inspect.signature(build_key).parameters) == {
        "experiment",
        "provider",
        "probe_type",
        "moment",
        "target",
        "uid",
        "suffix",
    }


LOGS_KEY_RE = re.compile(
    r"^experiment=[a-z0-9-]+/logs/provider=(aws|azure)/date=\d{4}-\d{2}-\d{2}/"
    r"\d{8}T\d{6}Z_[a-z0-9-]+_[0-9a-f-]{36}\.jsonl$"
)


def test_logs_key_follows_spec_0003_section_8_4():
    key = logs_key("ftg", "aws", MOMENT, "ip-10-0-0-1.ec2.internal", UID)
    assert key == (
        "experiment=ftg/logs/provider=aws/date=2026-10-11/"
        f"20261011T235958Z_ip-10-0-0-1-ec2-internal_{UID}.jsonl"
    )
    assert LOGS_KEY_RE.match(key)


def test_logs_key_never_matches_the_row_glob_shape():
    """The row glob is `experiment=*/provider=*/probe_type=*/date=*/*.jsonl` (spec 0006
    section 4.1): its second path segment is a literal `provider=`, which a logs key never
    has, because the second segment is the literal `logs`."""
    key = logs_key("ftg", "aws", MOMENT, "host", UID)
    second_segment = key.split("/")[1]
    assert second_segment == "logs"
    assert not second_segment.startswith("provider=")


def test_logs_key_makes_a_new_uuid_each_time():
    keys = {logs_key("ftg", "aws", MOMENT, "host") for _ in range(5)}
    assert len(keys) == 5


def test_raw_key_follows_spec_4_8():
    key = raw_key("ftg", "aws", "aws_spot_advisor", MOMENT, UID)
    assert key == (
        "experiment=ftg/provider=aws/probe_type=aws_spot_advisor/date=2026-10-11/"
        f"20261011T235958Z_raw_{UID}.json"
    )


def test_file_store_writes_under_the_key_and_never_overwrites(tmp_path):
    store = FileStore(tmp_path)
    key = build_key("ftg", "aws", "aws_spot_score", MOMENT, "x", UID)
    store.put(key, b"{}\n")
    assert (tmp_path / key).read_bytes() == b"{}\n"
    with pytest.raises(FileExistsError):
        store.put(key, b"again\n")
    assert (tmp_path / key).read_bytes() == b"{}\n"


class FakeS3:
    def __init__(self) -> None:
        self.calls = []

    def put_object(self, **kwargs):
        self.calls.append(kwargs)


class FakeContainer:
    def __init__(self) -> None:
        self.calls = []

    def upload_blob(self, **kwargs):
        self.calls.append(kwargs)


def test_s3_store_puts_with_the_prefix_and_a_no_overwrite_condition():
    client = FakeS3()
    store = open_store("s3://my-bucket/some/prefix/", client=client)
    assert isinstance(store, S3Store)
    assert store.url == "s3://my-bucket/some/prefix"
    store.put("a/b.jsonl", b"x")
    assert client.calls == [
        {"Bucket": "my-bucket", "Key": "some/prefix/a/b.jsonl", "Body": b"x", "IfNoneMatch": "*"}
    ]


def test_s3_store_without_a_prefix():
    client = FakeS3()
    open_store("s3://my-bucket", client=client).put("a.jsonl", b"x")
    assert client.calls[0]["Key"] == "a.jsonl"


def test_blob_store_uploads_with_overwrite_false():
    client = FakeContainer()
    store = open_store("az://myaccount/data/raw", client=client)
    assert isinstance(store, BlobStore)
    assert (store.account, store.container, store.prefix) == ("myaccount", "data", "raw")
    store.put("a/b.jsonl", b"x")
    assert client.calls == [{"name": "raw/a/b.jsonl", "data": b"x", "overwrite": False}]


def test_open_store_file_url(tmp_path):
    store = open_store(f"file://{tmp_path}")
    assert isinstance(store, FileStore)
    assert store.root == tmp_path


@pytest.mark.parametrize(
    "url", ["file://relative/path", "s3://", "az://account", "az:///container", "gs://x", "x"]
)
def test_open_store_rejects_bad_urls(url):
    with pytest.raises(ValueError):
        open_store(url)


def _race_factory(created: list[object]):
    def factory(*_args, **_kwargs):
        client = object()
        created.append(client)
        time.sleep(0.05)  # keeps the race window open for a thread without a lock
        return client

    return factory


def _clients_from_8_threads(store) -> set[int]:
    seen: list[int] = []
    gate = threading.Barrier(8)

    def worker() -> None:
        gate.wait()
        seen.append(id(store.client))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return set(seen)


def test_s3_store_builds_its_lazy_client_once_across_threads(monkeypatch):
    created: list[object] = []
    monkeypatch.setattr("ftg_prober.awsclient.boto3_client", _race_factory(created))
    assert len(_clients_from_8_threads(S3Store("bucket"))) == 1
    assert len(created) == 1


def test_blob_store_builds_its_lazy_client_once_across_threads(monkeypatch):
    created: list[object] = []
    factory = _race_factory(created)
    for name, attr, value in (
        ("azure", None, None),
        ("azure.storage", None, None),
        ("azure.storage.blob", "ContainerClient", factory),
        ("azure.identity", "DefaultAzureCredential", lambda: object()),
    ):
        module = types.ModuleType(name)
        if attr:
            setattr(module, attr, value)
        monkeypatch.setitem(sys.modules, name, module)
    assert len(_clients_from_8_threads(BlobStore("acct", "box"))) == 1
    assert len(created) == 1


def test_boto3_client_uses_its_own_session_not_the_default(monkeypatch):
    """https://boto3.amazonaws.com/v1/documentation/api/latest/guide/clients.html#multithreading-or-multiprocessing-with-clients"""
    sessions: list[object] = []

    class FakeSession:
        def __init__(self) -> None:
            sessions.append(self)

        def client(self, service, region_name=None, config=None):
            return (self, service, region_name)

    def default_client(*_args, **_kwargs):
        raise AssertionError("boto3.client() uses the shared default session")

    fake_boto3 = types.ModuleType("boto3")
    fake_boto3.client = default_client
    fake_session = types.ModuleType("boto3.session")
    fake_session.Session = FakeSession
    fake_boto3.session = fake_session
    fake_config = types.ModuleType("botocore.config")
    fake_config.Config = lambda **kwargs: kwargs
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    monkeypatch.setitem(sys.modules, "boto3.session", fake_session)
    monkeypatch.setitem(sys.modules, "botocore", types.ModuleType("botocore"))
    monkeypatch.setitem(sys.modules, "botocore.config", fake_config)

    first = boto3_client("s3")
    second = boto3_client("ec2", "us-east-1")
    assert first[1:] == ("s3", None)
    assert second[1:] == ("ec2", "us-east-1")
    assert len(sessions) == 2
    assert first[0] is not second[0]
