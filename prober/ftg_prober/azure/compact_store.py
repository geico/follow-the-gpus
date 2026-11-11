"""The Azure Blob backend of `ftg-prober compact` (ADR 0025).

It has 3 calls: list, get, put. It has no delete call and no other write path, and `put` refuses a
key outside `experiment=<name>/daily/v1/`. The identity is the VM's managed identity, through
`DefaultAzureCredential`, as in `ftg_prober.store.BlobStore`; it holds plain read (Storage Blob
Data Reader) and the existing write role on the data container.

It implements the `CompactStore` protocol of `ftg_prober.compact_job`; `open_compact_store` in
`ftg_prober.compact_stores` opens it for an `az://` URL.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlparse

_DAILY_KEY = re.compile(r"^experiment=[^/]+/daily/v1/")


class BlobCompactStore:
    """`az://<storage account>/<container>[/prefix]`, for the compaction job.

    `client` is an azure-storage-blob ContainerClient, or a fake with `list_blobs`,
    `download_blob`, and `upload_blob`.
    """

    def __init__(self, account: str, container: str, prefix: str = "", client: Any = None) -> None:
        self.account = account
        self.container = container
        self.prefix = prefix.strip("/")
        self._client = client
        self._client_lock = threading.Lock()
        self.url = f"az://{account}/{container}" + (f"/{self.prefix}" if self.prefix else "")

    @classmethod
    def from_url(cls, url: str, *, client: Any = None) -> BlobCompactStore:
        """Parse `az://<storage account>/<container>[/prefix]`, as `ftg_prober.store.open_store`."""
        parsed = urlparse(url)
        container, _, prefix = parsed.path.lstrip("/").partition("/")
        if parsed.scheme != "az" or not parsed.netloc or not container:
            raise ValueError(
                f"a Blob store URL must be az://<storage account>/<container>[/prefix], not {url!r}"
            )
        return cls(parsed.netloc, container, prefix, client=client)

    @property
    def client(self) -> Any:
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

    def _full(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    def list(self, prefix: str) -> Iterator[tuple[str, int]]:
        """Yield (key, size in bytes) for each blob whose name starts with `prefix`.

        The listing is flat (no delimiter) and follows every page.
        """
        strip = len(self.prefix) + 1 if self.prefix else 0
        pages = self.client.list_blobs(name_starts_with=self._full(prefix)).by_page()
        for page in pages:
            for blob in page:
                yield blob.name[strip:], blob.size

    def get(self, key: str) -> bytes:
        return self.client.download_blob(self._full(key)).readall()

    def put(self, key: str, data: bytes, content_type: str) -> None:
        """Write the blob, replacing one of the same name (a rebuild replaces a daily file).

        A key outside `experiment=<name>/daily/v1/` raises `ValueError`; the job's `_put`
        (compact_job.py) refuses it first, and this is a second guard.
        """
        if not _DAILY_KEY.match(key):
            raise ValueError(f"compact: refusing to write {key!r}, not under daily/v1/")
        from azure.storage.blob import ContentSettings

        self.client.upload_blob(
            name=self._full(key),
            data=data,
            overwrite=True,
            content_settings=ContentSettings(content_type=content_type),
        )
