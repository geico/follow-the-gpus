"""Storage backends behind 1 interface, and the object key builder of spec 0002 section 7.3."""

import re
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

from ftg_prober.record import Record

_SLUG_RE = re.compile(r"[^a-z0-9]+")


class Store(Protocol):
    """Write 1 object. Never overwrite."""

    url: str

    def put(self, key: str, data: bytes) -> None: ...


def slug(text: str) -> str:
    """Lower case, `[a-z0-9-]` only, no leading or trailing dash."""
    return _SLUG_RE.sub("-", text.lower()).strip("-") or "none"


def format_key_ts(moment: datetime) -> str:
    """The `ts` part of a key: `YYYYMMDDTHHMMSSZ`."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def target_for(rows: list[Record]) -> str:
    """`<sku>-<region>` of the rows. A part that differs between rows becomes `multi`."""
    skus = {row.sku for row in rows}
    regions = {row.region for row in rows}
    sku = slug(skus.pop()) if len(skus) == 1 else "multi"
    region = slug(regions.pop()) if len(regions) == 1 else "multi"
    return f"{sku}-{region}"


def build_key(
    experiment: str,
    provider: str,
    probe_type: str,
    moment: datetime,
    target: str,
    uid: uuid.UUID | None = None,
    suffix: str = "jsonl",
) -> str:
    """`experiment=<name>/provider=<p>/probe_type=<t>/date=<YYYY-MM-DD>/<ts>_<target>_<uuid>.<suffix>`.

    ADR 0010 section 5, item 4, kept by ADR 0011 section 6: `date` stays the last partition.
    A simulated row's key looks exactly like a real row's key; `simulated` is a row field
    (spec 0002 section 7.2), not a path segment.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    moment = moment.astimezone(UTC)
    uid = uid or uuid.uuid4()
    return (
        f"experiment={experiment}/provider={provider}/probe_type={probe_type}/"
        f"date={moment:%Y-%m-%d}/{format_key_ts(moment)}_{target}_{uid}.{suffix}"
    )


def raw_key(experiment: str, provider: str, probe_type: str, moment: datetime, uid=None) -> str:
    """The key of a raw feed object (spec 0002 section 4.8): `<ts>_raw_<uuid>.json`."""
    return build_key(experiment, provider, probe_type, moment, "raw", uid, suffix="json")


def logs_key(
    experiment: str, provider: str, moment: datetime, host: str, uid: uuid.UUID | None = None
) -> str:
    """`experiment=<name>/logs/provider=<p>/date=<YYYY-MM-DD>/<ts>_<host>_<uuid>.jsonl`.

    Spec 0003 section 8.2: the second path segment is `logs`, not `provider=`, so the row glob
    `experiment=*/provider=*/probe_type=*/date=*/*.jsonl` of spec 0006 section 4.1 never
    matches a logs object.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    moment = moment.astimezone(UTC)
    uid = uid or uuid.uuid4()
    return (
        f"experiment={experiment}/logs/provider={provider}/"
        f"date={moment:%Y-%m-%d}/{format_key_ts(moment)}_{slug(host)}_{uid}.jsonl"
    )


class FileStore:
    """`file:///abs/path`. Each key becomes a file under the root."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.url = f"file://{self.root}"

    def put(self, key: str, data: bytes) -> None:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as handle:
            handle.write(data)


class S3Store:
    """`s3://bucket[/prefix]`. `client` is a boto3 S3 client or a fake with `put_object`."""

    def __init__(self, bucket: str, prefix: str = "", client: Any = None) -> None:
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self._client = client
        self._client_lock = threading.Lock()
        self.url = f"s3://{bucket}" + (f"/{self.prefix}" if self.prefix else "")

    @property
    def client(self) -> Any:
        # The flush thread of the log handler and the scheduler thread can both come here first.
        with self._client_lock:
            if self._client is None:
                from ftg_prober.awsclient import boto3_client

                self._client = boto3_client("s3")
            return self._client

    def full_key(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    def put(self, key: str, data: bytes) -> None:
        self.client.put_object(
            Bucket=self.bucket, Key=self.full_key(key), Body=data, IfNoneMatch="*"
        )


class BlobStore:
    """`az://<storage account>/<container>[/prefix]`.

    `container` is an azure-storage-blob ContainerClient or a fake with `upload_blob`.
    """

    def __init__(self, account: str, container: str, prefix: str = "", client: Any = None) -> None:
        self.account = account
        self.container = container
        self.prefix = prefix.strip("/")
        self._client = client
        self._client_lock = threading.Lock()
        self.url = f"az://{account}/{container}" + (f"/{self.prefix}" if self.prefix else "")

    @property
    def client(self) -> Any:
        # Same race as S3Store.client: build the one ContainerClient under a lock. The Azure SDK
        # clients themselves are thread-safe once built.
        # https://learn.microsoft.com/en-us/azure/developer/python/sdk/fundamentals/http-pipeline-retries
        with self._client_lock:
            if self._client is None:
                from azure.identity import DefaultAzureCredential
                from azure.storage.blob import ContainerClient

                self._client = ContainerClient(
                    account_url=f"https://{self.account}.blob.core.windows.net",
                    container_name=self.container,
                    credential=DefaultAzureCredential(),
                )
            return self._client

    def full_key(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    def put(self, key: str, data: bytes) -> None:
        self.client.upload_blob(name=self.full_key(key), data=data, overwrite=False)


def open_store(url: str, *, client: Any = None) -> Store:
    """Make the store for a URL. `client` replaces the SDK client, for tests."""
    parsed = urlparse(url)
    if parsed.scheme == "file":
        if parsed.netloc not in ("", "localhost") or not parsed.path.startswith("/"):
            raise ValueError(f"a file store URL must be file:///abs/path, not {url!r}")
        return FileStore(parsed.path)
    if parsed.scheme == "s3":
        if not parsed.netloc:
            raise ValueError(f"an S3 store URL must be s3://bucket[/prefix], not {url!r}")
        return S3Store(parsed.netloc, parsed.path, client=client)
    if parsed.scheme == "az":
        container, _, prefix = parsed.path.lstrip("/").partition("/")
        if not parsed.netloc or not container:
            raise ValueError(
                f"a Blob store URL must be az://<storage account>/<container>[/prefix], not {url!r}"
            )
        return BlobStore(parsed.netloc, container, prefix, client=client)
    raise ValueError(f"unknown store URL scheme {parsed.scheme!r} in {url!r}; use file, s3, or az")
