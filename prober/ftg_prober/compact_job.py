"""The daily compaction job: list, read, call the core, write the data file, then its sidecar.
Spec 0003 sections 4.3 and 9B, ADR 0025. Cloud-neutral: it talks to a `CompactStore`.

The store interface (3 calls, keys relative to the store root):

    class CompactStore(Protocol):
        def list(self, prefix: str) -> Iterable[tuple[str, int]]: ...   # (key, size in bytes)
        def get(self, key: str) -> bytes: ...
        def put(self, key: str, data: bytes, content_type: str) -> None: ...

- `list` returns every key that starts with `prefix`, all pages, with its size.
- `get` raises when the key is missing or the read fails.
- `put` writes the object. There is no delete and no copy; the job never calls one.

The job writes only under `experiment=<name>/daily/v1/`, a new path, so the probe data stays as
it is. Every write goes through `_put`, which refuses any other key.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import defaultdict
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Protocol

from ftg_prober import compact
from ftg_prober.compact import objects_sha256_from_listing
from ftg_prober.log import kv

log = logging.getLogger("ftg_prober.compact_job")

# A day is closed this long after its UTC end (spec 0003 section 9B, item 2).
CLOSE_MARGIN = timedelta(minutes=30)
# Parallel reads of the raw objects of one (day, probe type).
READ_WORKERS = 4

_DATE = r"\d{4}-\d{2}-\d{2}"
_PROBE_TYPE = r"[a-z0-9_]+"  # starts with aws_ or az_


class CompactStore(Protocol):
    """See the top of this file."""

    def list(self, prefix: str) -> Iterable[tuple[str, int]]: ...

    def get(self, key: str) -> bytes: ...

    def put(self, key: str, data: bytes, content_type: str) -> None: ...


class WriteRefused(ValueError):
    """A write to a key that is not under `experiment=<name>/daily/v1/`."""


def daily_keys(experiment: str, probe_type: str, day: str) -> tuple[str, str]:
    """The ONE place that builds the daily key names: (data key, sidecar manifest key).

    `experiment=<name>/daily/v1/date=<D>/<probe_type>_<D>.jsonl.zst` and the same with
    `.manifest.json` added (written last; it marks the probe type's day complete).
    """
    data = f"experiment={experiment}/daily/v1/date={day}/{probe_type}_{day}.jsonl.zst"
    return data, data + ".manifest.json"


def _put(store: CompactStore, experiment: str, key: str, data: bytes, content_type: str) -> None:
    """The only write path of the job."""
    if not key.startswith(f"experiment={experiment}/daily/v1/") or ".." in key.split("/"):
        raise WriteRefused(f"refused to write {key!r}: not under experiment={experiment}/daily/v1/")
    store.put(key, data, content_type)


@dataclass
class Summary:
    built: int = 0
    unchanged: int = 0
    failed: int = 0
    failures: list[str] = field(default_factory=list)


def is_closed(day: str, now: datetime) -> bool:
    """True when `now` is 30 minutes or more after the UTC end of `day`."""
    end = datetime.combine(date.fromisoformat(day) + timedelta(days=1), datetime.min.time(), UTC)
    return now >= end + CLOSE_MARGIN


def compact_all(
    store: CompactStore,
    *,
    experiment: str,
    provider: str,
    now: datetime | None = None,
    workers: int = READ_WORKERS,
) -> Summary:
    """Build the daily file of every closed (day, probe type) that has no sidecar or whose sidecar
    does not match the listing. A failed day gets no sidecar; the caller exits 1 when
    `Summary.failed` is not 0."""
    now = (now or datetime.now(UTC)).astimezone(UTC)
    row_re = re.compile(
        rf"experiment={re.escape(experiment)}/provider={re.escape(provider)}/"
        rf"probe_type=({_PROBE_TYPE})/date=({_DATE})/[^/]+\.jsonl",
        re.ASCII,  # \d must not match non-ASCII digits
    )
    log.info("compact.start %s", kv(experiment=experiment, provider=provider))

    groups: dict[tuple[str, str], list[tuple[str, int]]] = defaultdict(list)
    for key, size in store.list(f"experiment={experiment}/provider={provider}/"):
        m = row_re.fullmatch(key)
        if m:
            try:
                is_closed(m.group(2), now)  # parses the date; 9999-12-31 overflows here
            except (
                ValueError,
                OverflowError,
            ):  # e.g. date=2026-13-45: skip the key, go on
                log.warning("compact.bad_date_skipped %s", kv(key=key))
                continue
            groups[(m.group(2), m.group(1))].append((key, size))
    daily = {key for key, _ in store.list(f"experiment={experiment}/daily/v1/")}

    summary = Summary()
    for day, probe_type in sorted(groups):
        if not is_closed(day, now):
            continue
        try:
            built = _one_day(
                store, daily, groups[(day, probe_type)], experiment, probe_type, day, workers
            )
        except Exception as exc:  # noqa: BLE001 - a failed day must not stop the other days
            summary.failed += 1
            summary.failures.append(f"{probe_type} {day}")
            log.error("compact.day_failed %s", kv(probe_type=probe_type, day=day, error=exc))
            continue
        if built:
            summary.built += 1
        else:
            summary.unchanged += 1
    log.info(
        "compact.done %s",
        kv(built=summary.built, unchanged=summary.unchanged, failed=summary.failed),
    )
    return summary


def _one_day(
    store: CompactStore,
    daily: set[str],
    listing: list[tuple[str, int]],
    experiment: str,
    probe_type: str,
    day: str,
    workers: int,
) -> bool:
    """Compact one (day, probe type). True when built, False when unchanged."""
    data_key, manifest_key = daily_keys(experiment, probe_type, day)
    digest = objects_sha256_from_listing(listing)

    if manifest_key in daily:  # existence comes from the listing
        try:
            if json.loads(store.get(manifest_key))["objects_sha256"] == digest:
                return False
        except (ValueError, KeyError, TypeError):
            pass  # an unreadable sidecar: build the day again

    keys = [key for key, _ in listing]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        bodies = list(zip(keys, pool.map(store.get, keys), strict=True))
    result = compact.compact_day(bodies)
    manifest = result.manifest
    if manifest["objects_sha256"] != digest:
        raise compact.CompactVerifyError(
            "the objects read do not match the listing (names or sizes changed during the run)"
        )

    _put(store, experiment, data_key, result.data, "application/zstd")
    back = store.get(data_key)
    if (
        hashlib.sha256(back).hexdigest() != manifest["file_sha256"]
        or len(back) != manifest["file_bytes"]
    ):
        raise compact.CompactVerifyError("the data file read back differs from the one written")
    compact.verify(back, manifest)
    # The sidecar goes last: a day without a sidecar is not complete.
    _put(
        store,
        experiment,
        manifest_key,
        (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(),
        "application/json",
    )
    log.info(
        "compact.day_built %s",
        kv(
            probe_type=probe_type,
            day=day,
            raw_object_count=manifest["raw_object_count"],
            row_count=manifest["row_count"],
            file_bytes=manifest["file_bytes"],
        ),
    )
    return True
