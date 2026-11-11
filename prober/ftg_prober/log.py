"""Logging to stdout, 1 line per event, for journalctl, and to the store's logs prefix."""

import ast
import collections
import contextlib
import json
import logging
import re
import socket
import sys
import threading
import time
import traceback
from datetime import UTC, datetime

from ftg_prober.record import format_ts
from ftg_prober.store import Store, logs_key


class _LineFormatter(logging.Formatter):
    """UTC time, level, logger, message. New lines in the message become spaces."""

    converter = time.gmtime

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        return " ".join(line.splitlines())


AZURE_LOGGER = "azure"


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        _LineFormatter(
            fmt="%(asctime)sZ %(levelname)s %(name)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"
        )
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # The Azure SDK logs 1 INFO line per token request and per HTTP request and response.
    # Keep it at WARNING, unless the level is DEBUG. NOTSET follows the root level.
    azure_level = logging.NOTSET if root.level == logging.DEBUG else logging.WARNING
    logging.getLogger(AZURE_LOGGER).setLevel(azure_level)


def kv(**fields: object) -> str:
    """Format fields as `key=value` pairs for 1 log line."""
    parts = []
    for key, value in fields.items():
        text = str(value).replace("\n", " ")
        if " " in text or text == "":
            text = repr(text)
        parts.append(f"{key}={text}")
    return " ".join(parts)


def log_provider_error(
    logger: logging.Logger,
    *,
    probe_type: str,
    region: str | None,
    code: str | None,
    message: str | None,
    status: int | str | None = None,
    event: str = "provider.error",
    **fields: object,
) -> None:
    """1 warning line for 1 failed provider call. The message is as the provider returned it.

    A row never holds the message: it goes to the log only (spec 0003 section 8).
    """
    logger.warning(
        "%s %s",
        event,
        kv(
            probe_type=probe_type,
            region=region,
            code=code,
            status=status,
            message=message,
            **fields,
        ),
    )


FLUSH_SECONDS = 60.0
DEFAULT_MAX_BUFFERED = 10_000
# 8 MiB: a record is usually a few hundred bytes, so 10,000 of them are about 3 MiB, but a long
# traceback or provider message can be several KiB. 8 MiB holds a full buffer of normal records
# with room to spare, stays small next to the VM's memory, and makes 1 flush a single modest
# PutObject (a single PutObject takes up to 5 GiB:
# https://docs.aws.amazon.com/AmazonS3/latest/API/API_PutObject.html).
DEFAULT_MAX_BUFFERED_BYTES = 8 * 1024 * 1024
CLOSE_JOIN_SECONDS = 5.0  # the background thread wakes at once on `_stop.set()`

# kv() wraps a value in repr() exactly when it has a space or is empty, so this regex, plus
# ast.literal_eval on the quoted branch, undoes that encoding losslessly: a quoted group is a
# valid Python string literal (single- or double-quoted, with backslash escapes), and a bare
# token never contains a space. Parsed only from `record.getMessage()` (the kv() text), never
# from the appended exception text below, which is prose, not key=value pairs.
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


def _exception_text(record: logging.LogRecord) -> str | None:
    """The formatted traceback of `record.exc_info`, cached on the record like the stdlib
    `Formatter` does, so the stdout handler and this one format it at most once."""
    if not record.exc_info:
        return None
    if not record.exc_text:
        record.exc_text = "".join(traceback.format_exception(*record.exc_info))
    return record.exc_text


CLOSE_FLUSH_SECONDS = 10.0  # the deadline of the final flush, under a unit's TimeoutStopSec


class StoreLogHandler(logging.Handler):
    """The second handler of spec 0003 section 8.2: every record, as 1 JSON object mapped to
    the OpenTelemetry log data model, buffered and flushed as 1 JSONL object to the logs
    prefix through `Store.put`, every `flush_seconds` and at `close()`.

    `emit` only formats the record and appends it to the buffer; the write itself happens on
    a background thread (or only in `flush_to_store`, when `flush_seconds` is 0), so a slow or
    failing store never delays the caller. A failed flush writes 1 stdout line and keeps the
    records for the next flush, dropping the oldest first once `max_buffered` records or
    `max_buffered_bytes` bytes are exceeded. This
    handler never raises into the caller.
    """

    def __init__(
        self,
        store: Store,
        experiment: str,
        provider: str,
        *,
        flush_seconds: float = FLUSH_SECONDS,
        max_buffered: int = DEFAULT_MAX_BUFFERED,
        max_buffered_bytes: int = DEFAULT_MAX_BUFFERED_BYTES,
        host: str | None = None,
    ) -> None:
        super().__init__()
        self.store = store
        self.experiment = experiment
        self.provider = provider
        self.max_buffered = max_buffered
        self.max_buffered_bytes = max_buffered_bytes
        self.host = host or socket.gethostname()
        # (line, size) pairs, and the sum of the sizes, so that a drop is O(1) and nothing is
        # encoded again under the lock. They always change together, under the lock.
        self._buffer: collections.deque[tuple[str, int]] = collections.deque()
        self._buffer_bytes = 0
        # Reentrant: a signal handler that logs while `emit` holds the lock must not deadlock.
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        if flush_seconds > 0:
            self._thread = threading.Thread(target=self._run, args=(flush_seconds,), daemon=True)
            self._thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line, size = self._to_json(record)
        except Exception:  # noqa: BLE001 - logging must never raise
            self.handleError(record)
            return
        with self._lock:
            self._buffer.append((line, size))
            self._buffer_bytes += size
            self._evict()

    def _evict(self) -> None:
        """Drop the oldest lines until both caps hold. The caller holds the lock."""
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
            "Timestamp": format_ts(datetime.fromtimestamp(record.created, UTC)),
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
        """Write the buffer as 1 JSONL object. Never raises."""
        with self._lock:
            if not self._buffer:
                return
            batch, batch_bytes = self._buffer, self._buffer_bytes
            self._buffer, self._buffer_bytes = collections.deque(), 0
        data = ("\n".join(line for line, _ in batch) + "\n").encode("utf-8")
        key = logs_key(self.experiment, self.provider, datetime.now(UTC), self.host)
        try:
            self.store.put(key, data)
        except Exception as exc:  # noqa: BLE001 - a flush failure must never raise
            print(f"ftg-prober: log flush failed, keeping {len(batch)} record(s): {exc!r}")
            with self._lock:
                batch.extend(self._buffer)
                self._buffer, self._buffer_bytes = batch, batch_bytes + self._buffer_bytes
                self._evict()

    def _run(self, flush_seconds: float) -> None:
        while not self._stop.wait(flush_seconds):
            self.flush_to_store()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=CLOSE_JOIN_SECONDS)
        # Bound the final flush, so that a hung store cannot hold the stop past the unit's
        # TimeoutStopSec. Records still buffered at the deadline are lost.
        with self._lock:
            pending = len(self._buffer)
        flusher = threading.Thread(target=self.flush_to_store, daemon=True)
        flusher.start()
        flusher.join(timeout=CLOSE_FLUSH_SECONDS)
        if flusher.is_alive():
            print(f"ftg-prober: final log flush timed out, {pending} record(s) lost")
        super().close()


def attach_store_handler(
    store: Store, experiment: str, provider: str, *, flush_seconds: float = FLUSH_SECONDS
) -> StoreLogHandler:
    """Attach a `StoreLogHandler` to the root logger, next to the stdout handler. The caller
    closes and removes it when the process is done (spec 0003 section 8.2)."""
    handler = StoreLogHandler(store, experiment, provider, flush_seconds=flush_seconds)
    logging.getLogger().addHandler(handler)
    return handler
