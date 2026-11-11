"""The AWS sweeper's copy of the store log handler (spec 0003 section 8.2, spec 0013 section 9).

The same record format as the prober's `StoreLogHandler` and the Azure sweeper's
`BlobLogHandler` (`sweeper/azure/sweeper/log_store.py`): the code below is that file, with the
Blob upload replaced by 1 S3 `PutObject`. The Lambda zip is built from `sweeper/aws/` alone
(spec 0013 section 2), so it cannot import the Azure copy; a shared module would need a build
step that copies it into both zips. `tests/test_log_store.py` checks that the shared functions
stay the same as the Azure copy's.

The Lambda function has no `logs:*` permission, so nothing reaches CloudWatch Logs: this handler
is the only place its log goes. The scheduler starts 1 invocation a minute, and Lambda can freeze
the process between invocations, so the handler keeps no background thread: `handler.py` flushes
it once at the end of every invocation.
"""

from __future__ import annotations

import ast
import collections
import contextlib
import json
import logging
import re
import socket
import traceback
import uuid
from datetime import UTC, datetime
from typing import Any

_SLUG_RE = re.compile(r"[^a-z0-9]+")


# Same kv() decoding as the prober's `_parse_kv` (`ftg_prober.log`).
# The `(?<!\w)` makes a key start only at the start of a word, so a long word with no `=` is
# scanned once, not once per start position (linear, not quadratic). It finds the same keys.
_KV_RE = re.compile(r"(?<!\w)(\w+)=('(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|\S*)")


def _parse_kv(message: str) -> dict[str, str]:
    """Best-effort parse of 1 `kv()`-formatted message back into a dict."""
    fields: dict[str, str] = {}
    for key, raw in _KV_RE.findall(message):
        if raw[:1] in ("'", '"'):
            with contextlib.suppress(ValueError, SyntaxError):
                raw = ast.literal_eval(raw)
        fields[key] = raw
    return fields


def _exception_text(record: logging.LogRecord) -> str | None:
    """The formatted traceback of `record.exc_info`, cached on the record like the stdlib
    `Formatter` does. Nothing in this package calls `logger.exception` today, but a provider
    error's text must never be dropped if one does (spec 0003 section 8.2)."""
    if not record.exc_info:
        return None
    if not record.exc_text:
        record.exc_text = "".join(traceback.format_exception(*record.exc_info))
    return record.exc_text


# 8 MiB: the same cap as the prober's (`ftg_prober.log`). 10,000 normal records are about 3 MiB,
# but a long traceback or provider message can be several KiB.
DEFAULT_MAX_BUFFERED_BYTES = 8 * 1024 * 1024


TRUNCATED_MARK = "...[truncated]"
MAX_RECORD_BYTES = 64 * 1024
FIELD_CHARS = 256  # the longest an Attribute value, `run_id`, or `logger` stays in a huge record
# A message longer than this (1 MiB) is not parsed for `key=value` pairs: it is far over the record
# limit anyway, and its Attributes would be cut to the logger name. Its Body is still cut.
MAX_PARSE_CHARS = 1024 * 1024


def _dumps(obj: dict) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=True)


def _cut(text: object) -> object:
    if isinstance(text, str) and len(text) > FIELD_CHARS:
        return text[:FIELD_CHARS] + TRUNCATED_MARK
    return text


def _fit(text: str, room: int) -> str:
    """The longest prefix of `text` whose JSON-escaped form (without the quotes) is at most
    `room` characters long."""
    low, high = 0, min(len(text), max(room, 0))  # an escaped character takes at least 1
    while low < high:
        mid = (low + high + 1) // 2
        if len(_dumps(text[:mid])) - 2 <= room:
            low = mid
        else:
            high = mid - 1
    return text[:low]


def _serialize(obj: dict, max_bytes: int) -> tuple[str, int]:
    """The JSON line of a record and its size in the JSONL object (the line and 1 newline).
    `ensure_ascii` makes the line pure ASCII, so its length is its byte count.

    1 record may take at most `min(max_bytes // 64, 64 KiB)` bytes, so that 1 huge record
    cannot evict the history. A longer record first loses the tail of its long string fields
    (the Attribute values, `run_id`, and the `logger` name, to 256 characters), then every
    Attribute but the `logger` name, then (only if still too long) the tail of `experiment`,
    `provider`, and `host`, then the tail of its Body, by its JSON-escaped length. Each cut
    field ends with `...[truncated]`."""
    line = _dumps(obj)
    size = len(line) + 1
    limit = min(max_bytes // 64, MAX_RECORD_BYTES)
    if size <= limit:
        return line, size
    obj = dict(obj)
    obj["Attributes"] = {key: _cut(value) for key, value in obj["Attributes"].items()}
    obj["run_id"] = _cut(obj["run_id"])
    line = _dumps(obj)
    if len(line) + 1 > limit:
        # Many attributes or a huge key: keep only the logger name.
        obj["Attributes"] = {"logger": obj["Attributes"].get("logger")}
        line = _dumps(obj)
    if len(line) + 1 > limit:
        # Still too long: cut the three identifying fields, which only matters at a small cap.
        for name in ("experiment", "provider", "host"):
            obj[name] = _cut(obj[name])
        line = _dumps(obj)
    if len(line) + 1 > limit:
        body = obj["Body"]
        # The room is in JSON-escaped characters, not in characters of the Body.
        room = len(_dumps(body)) - 2 - (len(line) + 1 - limit) - len(TRUNCATED_MARK)
        obj["Body"] = _fit(body, room) + TRUNCATED_MARK
        line = _dumps(obj)
    return line, len(line) + 1


def _slug(text: str) -> str:
    return _SLUG_RE.sub("-", text.lower()).strip("-") or "none"


def _format_ts(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    moment = moment.astimezone(UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def logs_key(experiment: str, provider: str, moment: datetime, host: str) -> str:
    """`experiment=<name>/logs/provider=<p>/date=<YYYY-MM-DD>/<ts>_<host>_<uuid>.jsonl`, the
    same shape as the prober's (`ftg_prober.store.logs_key`)."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    moment = moment.astimezone(UTC)
    ts = moment.strftime("%Y%m%dT%H%M%SZ")
    return (
        f"experiment={experiment}/logs/provider={provider}/"
        f"date={moment:%Y-%m-%d}/{ts}_{_slug(host)}_{uuid.uuid4()}.jsonl"
    )


class S3LogHandler(logging.Handler):
    """Buffers every record as 1 JSON object. `flush_to_store` writes the buffer as 1 JSONL
    object and never raises; a failed flush writes 1 stdout line and keeps the records, capped
    at `max_buffered` records and `max_buffered_bytes` bytes (oldest dropped first), for the next
    flush."""

    def __init__(
        self,
        client: Any,
        bucket: str,
        experiment: str,
        *,
        provider: str = "aws",
        max_buffered: int = 10_000,
        max_buffered_bytes: int = DEFAULT_MAX_BUFFERED_BYTES,
        host: str | None = None,
    ) -> None:
        super().__init__()
        self.client = client
        self.bucket = bucket
        self.experiment = experiment
        self.provider = provider
        self.max_buffered = max_buffered
        self.max_buffered_bytes = max_buffered_bytes
        self.host = host or socket.gethostname()
        # (line, size) pairs and the sum of the sizes: a drop is O(1), nothing is encoded again.
        self._buffer: collections.deque[tuple[str, int]] = collections.deque()
        self._buffer_bytes = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line, size = self._to_json(record)
        except Exception:  # noqa: BLE001 - logging must never raise
            self.handleError(record)
            return
        self._buffer.append((line, size))
        self._buffer_bytes += size
        self._evict()

    def _evict(self) -> None:
        """Drop the oldest lines until both caps hold."""
        while self._buffer and (
            len(self._buffer) > self.max_buffered or self._buffer_bytes > self.max_buffered_bytes
        ):
            self._buffer_bytes -= self._buffer.popleft()[1]

    def _to_json(self, record: logging.LogRecord) -> tuple[str, int]:
        kv_message = record.getMessage()
        body = kv_message
        exc_text = _exception_text(record)
        if exc_text:
            body = f"{body}\n{exc_text}"
        body = " ".join(body.splitlines())
        parsed = _parse_kv(kv_message) if len(kv_message) <= MAX_PARSE_CHARS else {}
        attributes = {"logger": record.name, **parsed}
        run_id = attributes.pop("run_id", None)
        obj = {
            "Timestamp": _format_ts(datetime.fromtimestamp(record.created, UTC)),
            "SeverityText": record.levelname,
            "Body": body,
            "Attributes": attributes,
            "run_id": run_id,
            "experiment": self.experiment,
            "provider": self.provider,
            "host": self.host,
        }
        return _serialize(obj, self.max_buffered_bytes)

    def flush_to_store(self) -> None:
        if not self._buffer:
            return
        batch, batch_bytes = self._buffer, self._buffer_bytes
        self._buffer, self._buffer_bytes = collections.deque(), 0
        data = ("\n".join(line for line, _ in batch) + "\n").encode("utf-8")
        key = logs_key(self.experiment, self.provider, datetime.now(UTC), self.host)
        try:
            # No `IfNoneMatch`: the Lambda runtime's boto3 version is not ours to pin (spec 0013
            # section 9), and the key ends in a new UUID, so it never names an existing object.
            self.client.put_object(Bucket=self.bucket, Key=key, Body=data)
        except Exception as exc:  # noqa: BLE001 - a flush failure must never raise
            print(f"sweeper: log flush failed, keeping {len(batch)} record(s): {exc!r}")
            batch.extend(self._buffer)
            self._buffer, self._buffer_bytes = batch, batch_bytes + self._buffer_bytes
            self._evict()


def attach_s3_log_handler(
    logger: logging.Logger, client: Any, bucket: str, experiment: str, host: str | None = None
) -> S3LogHandler:
    """Attach 1 `S3LogHandler` to `logger`. The caller flushes it once per invocation."""
    handler = S3LogHandler(client, bucket, experiment, host=host)
    logger.addHandler(handler)
    return handler
