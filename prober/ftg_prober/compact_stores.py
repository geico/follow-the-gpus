"""The backends of `CompactStore` (see compact_job.py): a folder for tests, S3, and Azure Blob
(`ftg_prober.azure.compact_store`).

Neither class has a delete or a copy method, and the job could not call one if it had.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


class FileCompactStore:
    """`file:///abs/path`. For tests."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.url = f"file://{self.root}"

    def list(self, prefix: str) -> Iterator[tuple[str, int]]:
        if not self.root.is_dir():
            return
        for path in sorted(self.root.rglob("*")):
            if path.is_file():
                key = path.relative_to(self.root).as_posix()
                if key.startswith(prefix):
                    yield key, path.stat().st_size

    def get(self, key: str) -> bytes:
        return (self.root / key).read_bytes()

    def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


class S3CompactStore:
    """`s3://bucket[/prefix]`. `client` is a boto3 S3 client, or a fake with the same calls.

    `get_paginator("list_objects_v2").paginate(Bucket, Prefix)` yields pages whose `Contents` is
    ABSENT (not empty) when nothing matches; `get_object(Bucket, Key)["Body"].read()`;
    `put_object(Bucket, Key, Body, ContentType)`.
    """

    def __init__(self, bucket: str, prefix: str = "", client: Any = None) -> None:
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self._client = client
        self._client_lock = threading.Lock()
        self.url = f"s3://{bucket}" + (f"/{self.prefix}" if self.prefix else "")

    @property
    def client(self) -> Any:
        with self._client_lock:
            if self._client is None:
                from ftg_prober.awsclient import boto3_client

                self._client = boto3_client("s3")
            return self._client

    def _full(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    def list(self, prefix: str) -> Iterator[tuple[str, int]]:
        strip = len(self.prefix) + 1 if self.prefix else 0
        pages = self.client.get_paginator("list_objects_v2").paginate(
            Bucket=self.bucket, Prefix=self._full(prefix)
        )
        for page in pages:
            for obj in page.get("Contents", []):
                yield obj["Key"][strip:], obj["Size"]

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=self._full(key))["Body"].read()

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.client.put_object(
            Bucket=self.bucket, Key=self._full(key), Body=data, ContentType=content_type
        )


def open_compact_store(url: str, *, client: Any = None) -> Any:
    parsed = urlparse(url)
    if parsed.scheme == "file":
        if parsed.netloc not in ("", "localhost") or not parsed.path.startswith("/"):
            raise ValueError(f"a file store URL must be file:///abs/path, not {url!r}")
        return FileCompactStore(parsed.path)
    if parsed.scheme == "s3":
        if not parsed.netloc:
            raise ValueError(f"an S3 store URL must be s3://bucket[/prefix], not {url!r}")
        return S3CompactStore(parsed.netloc, parsed.path, client=client)
    if parsed.scheme == "az":
        from ftg_prober.azure.compact_store import BlobCompactStore

        return BlobCompactStore.from_url(url, client=client)
    raise ValueError(f"compact: store URL {url!r} must use file, s3, or az")
