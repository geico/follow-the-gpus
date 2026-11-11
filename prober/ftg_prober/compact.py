"""Daily compaction core: pack the raw row objects of one (day, provider, probe type) into one
zstd JSONL file plus a manifest. No cloud call: the caller lists and fetches the
objects and writes the result.

The packing matches `scripts/compact-rows.sh` (spec 0006, section 5.5): one object per base name,
the `experiment=` key preferred over the old `run_mode=` key; the lines of the kept objects in
base-name order, byte for byte; a newline added after a last line that lacks one; blank lines
kept; an empty object adds nothing.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import zstandard

from ftg_prober import __version__

# Level 19 was chosen from a measurement on the local mirror: on 1015 objects
# (621,668 bytes) of one day, level 3 / 9 / 19 gave 54,307 / 45,146 / 41,696 bytes in
# 0.01 / 0.01 / 0.18 s; on 384 objects (266,498 bytes), 12,532 / 8,969 / 8,087 bytes.
DEFAULT_LEVEL = 19


class CompactVerifyError(Exception):
    """The compressed output does not match its manifest."""


@dataclass(frozen=True)
class Compacted:
    data: bytes  # the bytes of rows.jsonl.zst: one zstd frame with a content checksum
    manifest: dict[str, Any]


def _base_name(key: str) -> str:
    return key.rsplit("/", 1)[-1]


def _preferred(key: str) -> bool:
    """True when a folder of the key path (not the file name) starts with `experiment=`."""
    return any(seg.startswith("experiment=") for seg in key.split("/")[:-1])


def _group(key: str) -> tuple[str | None, str | None, str | None]:
    segs = key.split("/")[:-1]

    def find(prefix: str) -> str | None:
        return next((s for s in segs if s.startswith(prefix)), None)

    return find("provider="), find("probe_type="), find("date=")


def _select(items: Iterable[tuple[str, Any]]) -> tuple[int, list[tuple[str, Any]]]:
    """Return (raw object count, kept (base name, value) pairs sorted by base name).

    Only keys that end in `.jsonl` are rows (as in compact-rows.sh). One winner per base name:
    an `experiment=` key beats any other; among equal keys the smallest key wins, so the result
    does not depend on the input order. Input from more than one (day, provider, probe type)
    raises ValueError.
    """
    raw = 0
    groups = set()
    win: dict[str, tuple[bool, str, Any]] = {}
    for key, value in items:
        if not key.endswith(".jsonl"):
            continue
        raw += 1
        groups.add(_group(key))
        if len(groups) > 1:
            raise ValueError(f"input holds more than one (provider, probe type, day): {groups}")
        name = _base_name(key)
        cur = win.get(name)
        if cur is None or (_preferred(key), cur[1]) > (cur[0], key):
            win[name] = (_preferred(key), key, value)
    return raw, [(n, win[n][2]) for n in sorted(win, key=lambda n: n.encode())]


def _digest(named_sizes: list[tuple[str, int]]) -> str:
    h = hashlib.sha256()
    for name, size in named_sizes:
        # Escape the framing bytes so that no 2 lists give the same stream. A name without a
        # backslash, tab, or newline (every prober key) hashes as before.
        esc = name.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n")
        h.update(f"{esc}\t{size}\n".encode())
    return h.hexdigest()


def objects_sha256_from_listing(listing: Iterable[tuple[str, int]]) -> str:
    """The `objects_sha256` of a store listing of (key, size) pairs.

    The digest covers the sorted kept base names and sizes only, not the content, so a job can
    compare it with a listing. compact_day uses the same function path, so both agree.
    """
    _, kept = _select(listing)
    return _digest(kept)


def _raw_lines(body: bytes) -> bytes:
    if body and not body.endswith(b"\n"):
        return body + b"\n"
    return body


def _ts(line: bytes) -> str | None:
    """The ts_utc of a row, or None when it is missing, not a string, naive, or unparsable."""
    try:
        ts = json.loads(line)["ts_utc"]
        if not isinstance(ts, str) or datetime.fromisoformat(ts).tzinfo is None:
            return None
    except (ValueError, KeyError, TypeError, RecursionError):
        return None
    return ts


def _ts_summary(raw: bytes) -> tuple[int, str | None, str | None]:
    """(rows without a usable ts_utc, earliest ts_utc, latest ts_utc) of the rows in `raw`."""
    stamps: list[tuple[datetime, str]] = []
    unparsed = 0
    for line in raw.split(b"\n")[:-1]:
        ts = _ts(line)
        if ts is None:
            unparsed += 1
        else:
            stamps.append((datetime.fromisoformat(ts), ts))
    return unparsed, (min(stamps)[1] if stamps else None), (max(stamps)[1] if stamps else None)


def compact_day(
    objects: Iterable[tuple[str, bytes]],
    *,
    level: int = DEFAULT_LEVEL,
    now: datetime | None = None,
) -> Compacted:
    """Build rows.jsonl.zst and its manifest, and verify them against each other.

    `objects_sha256` is the digest of the sorted `name<TAB>size` lines of the kept objects (not
    their content); see objects_sha256_from_listing. `rows_sha256` is the sha256 of the
    uncompressed bytes; `file_bytes` and `file_sha256` describe the compressed file.
    """
    raw_count, kept = _select(objects)
    raw = b"".join(_raw_lines(body) for _, body in kept)
    unparsed, first, last = _ts_summary(raw)
    built = (now or datetime.now(UTC)).astimezone(UTC)
    manifest: dict[str, Any] = {
        "raw_object_count": raw_count,
        "kept_object_count": len(kept),
        "row_count": raw.count(b"\n"),
        "rows_bytes": len(raw),
        "rows_sha256": hashlib.sha256(raw).hexdigest(),
        "unparsed_row_count": unparsed,
        "first_ts_utc": first,
        "last_ts_utc": last,
        "objects_sha256": _digest([(n, len(b)) for n, b in kept]),
        "ftg_prober_version": __version__,
        "built_utc": built.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    data = zstandard.ZstdCompressor(level=level, write_checksum=True).compress(raw)
    manifest["file_bytes"] = len(data)
    manifest["file_sha256"] = hashlib.sha256(data).hexdigest()
    verify(data, manifest)
    return Compacted(data, manifest)


def verify(data: bytes, manifest: dict[str, Any]) -> None:
    """Decompress `data` and compare it with the manifest.

    Raises CompactVerifyError on a manifest that is not a dict, a damaged or truncated frame (the
    frame carries a checksum), bytes after the frame, a missing manifest key, a wrong type (an int
    field must be an int, not a float or a bool), or a difference in row_count, rows_bytes,
    rows_sha256, unparsed_row_count, first_ts_utc, last_ts_utc (recomputed from the decompressed
    rows as the writer does), file_bytes, or file_sha256 (the compressed file itself: a flipped
    bit that leaves the decoded rows unchanged is still caught). A job calls this before it
    writes the manifest. It reads and compares only; it never writes or deletes.
    """
    if not isinstance(manifest, dict):
        raise CompactVerifyError(f"the manifest is not a JSON object: {type(manifest).__name__}")

    def check(key: str, got: Any) -> None:
        if key not in manifest:
            raise CompactVerifyError(f"the manifest has no {key}")
        have = manifest[key]
        # type() is exact: True == 1 and 60.0 == 60 in Python, and neither is a valid count.
        if type(have) is not type(got) or have != got:
            raise CompactVerifyError(f"{key}: output has {got!r}, manifest says {have!r}")

    check("file_bytes", len(data))
    check("file_sha256", hashlib.sha256(data).hexdigest())
    d = zstandard.ZstdDecompressor().decompressobj()
    try:
        raw = d.decompress(data)
    except zstandard.ZstdError as exc:
        raise CompactVerifyError(f"cannot decompress the output: {exc}") from exc
    if not d.eof:
        raise CompactVerifyError("the output is truncated: the zstd frame is not complete")
    if d.unused_data:
        raise CompactVerifyError("the output holds bytes after the zstd frame")
    check("row_count", raw.count(b"\n"))
    check("rows_bytes", len(raw))
    check("rows_sha256", hashlib.sha256(raw).hexdigest())
    unparsed, first, last = _ts_summary(raw)
    check("unparsed_row_count", unparsed)
    check("first_ts_utc", first)
    check("last_ts_utc", last)
    if raw and not raw.endswith(b"\n"):
        raise CompactVerifyError("the output does not end with a newline")
