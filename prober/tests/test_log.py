"""Logging setup. The Azure SDK loggers are quiet unless the level is DEBUG."""

import json
import logging
import os
import subprocess
import sys
import threading
import time

import pytest

from ftg_prober.log import StoreLogHandler, _parse_kv, attach_store_handler, kv, setup_logging

AZURE_LOGGERS = (
    "azure",
    "azure.identity",
    "azure.core.pipeline.policies.http_logging_policy",
)


@pytest.fixture(autouse=True)
def _restore_logging():
    root = logging.getLogger()
    saved = (root.level, root.handlers[:], logging.getLogger("azure").level)
    yield
    root.setLevel(saved[0])
    root.handlers[:] = saved[1]
    logging.getLogger("azure").setLevel(saved[2])


@pytest.mark.parametrize("level", ["INFO", "info", "WARNING", "ERROR"])
def test_azure_loggers_do_not_log_info(level: str) -> None:
    setup_logging(level)
    for name in AZURE_LOGGERS:
        assert not logging.getLogger(name).isEnabledFor(logging.INFO), name


def test_azure_loggers_keep_warning() -> None:
    setup_logging("INFO")
    for name in AZURE_LOGGERS:
        assert logging.getLogger(name).isEnabledFor(logging.WARNING), name


def test_azure_loggers_level_is_warning_by_default() -> None:
    setup_logging("INFO")
    assert logging.getLogger("azure").level == logging.WARNING
    assert logging.getLogger("azure.identity").getEffectiveLevel() == logging.WARNING
    http = logging.getLogger("azure.core.pipeline.policies.http_logging_policy")
    assert http.getEffectiveLevel() == logging.WARNING


def test_debug_restores_azure_loggers() -> None:
    setup_logging("DEBUG")
    for name in AZURE_LOGGERS:
        assert logging.getLogger(name).isEnabledFor(logging.DEBUG), name
        assert logging.getLogger(name).isEnabledFor(logging.INFO), name


def test_setup_again_after_debug_quiets_azure_loggers() -> None:
    setup_logging("debug")
    setup_logging("INFO")
    assert not logging.getLogger("azure.identity").isEnabledFor(logging.INFO)


def test_own_loggers_still_log_info() -> None:
    setup_logging("INFO")
    assert logging.getLogger("ftg_prober.scheduler").isEnabledFor(logging.INFO)


class FakeStore:
    """Records each `put`. `fail` makes every call raise, like a down store."""

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str, bytes]] = []
        self.fail = fail

    def put(self, key: str, data: bytes) -> None:
        if self.fail:
            raise RuntimeError("store is down")
        self.calls.append((key, data))


def _handler(store, **kwargs) -> StoreLogHandler:
    """A handler with no background thread, for a test that flushes by hand."""
    return StoreLogHandler(store, "ftg", "aws", flush_seconds=0, **kwargs)


def test_emit_buffers_and_flush_writes_one_jsonl_object() -> None:
    store = FakeStore()
    handler = _handler(store)
    logger = logging.getLogger("ftg_prober.test_log")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        logger.error("probe.failed probe_type=aws_spot_score run_id=r-1 error='boom'")
        logger.info("probe.done probe_type=aws_spot_score run_id=r-1 rows=1 ms=5")
        handler.flush_to_store()
    finally:
        logger.removeHandler(handler)
    assert len(store.calls) == 1
    key, data = store.calls[0]
    assert key.startswith("experiment=ftg/logs/provider=aws/date=")
    assert key.endswith(".jsonl")
    lines = [json.loads(line) for line in data.decode("utf-8").splitlines()]
    assert len(lines) == 2
    assert lines[0]["SeverityText"] == "ERROR"
    assert lines[0]["Body"] == "probe.failed probe_type=aws_spot_score run_id=r-1 error='boom'"
    assert lines[0]["run_id"] == "r-1"
    assert lines[0]["experiment"] == "ftg"
    assert lines[0]["provider"] == "aws"
    assert lines[0]["Attributes"] == {
        "logger": "ftg_prober.test_log",
        "probe_type": "aws_spot_score",
        "error": "boom",
    }
    assert lines[1]["run_id"] == "r-1"
    assert lines[1]["Attributes"] == {
        "logger": "ftg_prober.test_log",
        "probe_type": "aws_spot_score",
        "rows": "1",
        "ms": "5",
    }


def test_attributes_unwraps_a_space_quoted_kv_value() -> None:
    """kv() repr()-quotes a value with a space; Attributes gets the field back unquoted,
    whichever quote character repr() picks (single, or double when the text has a single
    quote of its own)."""
    store = FakeStore()
    handler = _handler(store)
    logger = logging.getLogger("ftg_prober.test_log_quoted")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    text = "ValueError('bad thing')"  # has an embedded quote: repr() switches to "..."
    message = f"probe.failed probe_type=aws_spot_score {kv(error=text)}"
    try:
        logger.error(message)
        handler.flush_to_store()
    finally:
        logger.removeHandler(handler)
    (line,) = [json.loads(x) for x in store.calls[0][1].decode("utf-8").splitlines()]
    assert line["Attributes"]["error"] == text


def test_body_carries_the_exception_text_unfiltered() -> None:
    """A provider error's text is written exactly as returned (spec 0003 section 8.2): the
    store copy must not drop the traceback that `log.exception` attaches, the way stdout's
    `_LineFormatter` keeps it (collapsed to 1 line)."""
    store = FakeStore()
    handler = _handler(store)
    logger = logging.getLogger("ftg_prober.test_log_exc")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        try:
            raise ValueError("boom: quota exceeded for region us-east-1")
        except ValueError:
            logger.exception("probe.failed probe_type=aws_spot_score run_id=r-1 error=repr(exc)")
        handler.flush_to_store()
    finally:
        logger.removeHandler(handler)
    (line,) = [json.loads(x) for x in store.calls[0][1].decode("utf-8").splitlines()]
    assert "boom: quota exceeded for region us-east-1" in line["Body"]
    assert "Traceback (most recent call last)" in line["Body"]
    assert "ValueError" in line["Body"]


def test_flush_with_an_empty_buffer_writes_nothing() -> None:
    store = FakeStore()
    handler = _handler(store)
    handler.flush_to_store()
    assert store.calls == []


def test_a_failed_flush_never_raises_and_keeps_the_records() -> None:
    """Spec 0003 section 8.2: a failed flush writes 1 stdout line and keeps the records for
    the next flush. It must never raise into the scheduler loop."""
    store = FakeStore(fail=True)
    handler = _handler(store)
    logger = logging.getLogger("ftg_prober.test_log_fail")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        logger.info("probe.done probe_type=aws_spot_score run_id=r-1 rows=1 ms=5")
        handler.flush_to_store()  # must not raise
    finally:
        logger.removeHandler(handler)
    assert store.calls == []
    store.fail = False
    handler.flush_to_store()
    assert len(store.calls) == 1  # the kept record reaches the store on the next flush


def test_the_buffer_is_capped_so_a_down_store_cannot_grow_it_forever() -> None:
    store = FakeStore(fail=True)
    handler = _handler(store, max_buffered=3)
    logger = logging.getLogger("ftg_prober.test_log_cap")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        for i in range(5):
            logger.info(f"probe.done probe_type=aws_spot_score run_id=r-{i} rows=1 ms=5")
        handler.flush_to_store()
    finally:
        logger.removeHandler(handler)
    assert len(handler._buffer) == 3


def test_the_buffer_is_capped_in_bytes_and_drops_the_oldest_first() -> None:
    store = FakeStore(fail=True)
    handler = _handler(store, max_buffered_bytes=64_000)
    logger = logging.getLogger("ftg_prober.test_log_byte_cap")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        for i in range(100):
            logger.info("probe.error run_id=r-%d detail=%s", i, "x" * 800)
        buffered = [line for line, _ in handler._buffer]
    finally:
        logger.removeHandler(handler)
    assert 0 < len(buffered) < 100
    assert sum(len(line.encode()) + 1 for line in buffered) <= 64_000
    _assert_running_total(handler)
    assert json.loads(buffered[-1])["run_id"] == "r-99"  # the newest stays
    assert json.loads(buffered[0])["run_id"] != "r-0"  # the oldest went


def test_the_byte_cap_holds_after_a_failed_flush_puts_the_batch_back() -> None:
    store = FakeStore(fail=True)
    handler = _handler(store, max_buffered_bytes=64_000)
    logger = logging.getLogger("ftg_prober.test_log_byte_cap_flush")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        for i in range(60):
            logger.info("probe.error run_id=r-%d detail=%s", i, "x" * 800)
        handler.flush_to_store()  # fails, keeps the batch
        for i in range(60, 100):
            logger.info("probe.error run_id=r-%d detail=%s", i, "x" * 800)
        handler.flush_to_store()
    finally:
        logger.removeHandler(handler)
    assert handler._buffer_bytes <= 64_000
    _assert_running_total(handler)
    assert json.loads(handler._buffer[-1][0])["run_id"] == "r-99"


def _assert_running_total(handler) -> None:
    sizes = [len(line.encode()) + 1 for line, _ in handler._buffer]
    assert [size for _, size in handler._buffer] == sizes
    assert handler._buffer_bytes == sum(sizes)


def test_one_record_over_the_byte_cap_is_truncated_and_older_records_stay() -> None:
    store = FakeStore(fail=True)
    handler = _handler(store, max_buffered_bytes=1_000_000)
    logger = logging.getLogger("ftg_prober.test_log_huge")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        for i in range(5):
            logger.info("probe.error run_id=r-%d", i)
        logger.info("probe.error run_id=r-big detail=%s", "y" * 20_000)
    finally:
        logger.removeHandler(handler)
    lines = [json.loads(line) for line, _ in handler._buffer]
    assert [obj["run_id"] for obj in lines] == ["r-0", "r-1", "r-2", "r-3", "r-4", "r-big"]
    assert lines[-1]["Body"].endswith("...[truncated]")
    assert handler._buffer[-1][1] <= 15_625  # min(1_000_000 // 64, 64 KiB)
    _assert_running_total(handler)


def test_the_running_total_matches_the_line_sizes_after_drops_flushes_and_a_failed_flush() -> None:
    store = FakeStore(fail=True)
    handler = _handler(store, max_buffered=4, max_buffered_bytes=64_000)
    logger = logging.getLogger("ftg_prober.test_log_total")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        for i in range(8):
            logger.info("probe.error run_id=r-%d detail=%s", i, "z" * 300)
            _assert_running_total(handler)
        handler.flush_to_store()  # fails: the batch goes back
        _assert_running_total(handler)
        logger.info("probe.error run_id=r-8 detail=%s", "z" * 300)
        _assert_running_total(handler)
        store.fail = False
        handler.flush_to_store()  # succeeds: the buffer is empty
        assert handler._buffer_bytes == 0
        assert not handler._buffer
    finally:
        logger.removeHandler(handler)


def test_close_flushes_the_buffer() -> None:
    store = FakeStore()
    handler = _handler(store)
    logger = logging.getLogger("ftg_prober.test_log_close")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        logger.info("run.start provider=aws")
        handler.close()
    finally:
        logger.removeHandler(handler)
    assert len(store.calls) == 1


def test_emit_never_blocks_even_when_the_store_is_slow() -> None:
    """Spec 0003 section 8.2: a flush failure, or a slow store, must not stop or
    delay a probe. `emit` only formats the record and appends it to the buffer; the write
    itself happens on the background thread, so a blocking `put` cannot block a probe's own
    `log.info`/`log.exception` call."""
    release = threading.Event()

    class SlowStore:
        def put(self, key: str, data: bytes) -> None:
            release.wait(timeout=5.0)

    handler = StoreLogHandler(SlowStore(), "ftg", "aws", flush_seconds=0.01)
    logger = logging.getLogger("ftg_prober.test_log_slow")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        time.sleep(0.05)  # let the background thread start its (now blocked) flush
        started = time.perf_counter()
        logger.info("probe.done probe_type=aws_spot_score run_id=r-1 rows=1 ms=5")
        elapsed = time.perf_counter() - started
        assert elapsed < 0.1, f"emit() took {elapsed}s while the store's put() was blocked"
    finally:
        release.set()
        logger.removeHandler(handler)
        handler.close()


def test_attach_store_handler_adds_it_to_the_root_logger() -> None:
    store = FakeStore()
    handler = attach_store_handler(store, "ftg", "aws", flush_seconds=0)
    try:
        assert handler in logging.getLogger().handlers
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()


def test_sdk_records_reach_the_store_like_ours() -> None:
    store = FakeStore()
    setup_logging("INFO")
    handler = attach_store_handler(store, "ftg", "aws", flush_seconds=0)
    try:
        logging.getLogger("botocore.endpoint").warning("sdk warning")
        logging.getLogger("ftg_prober.cli").info("our line")
        handler.flush_to_store()
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()
    ((_, data),) = store.calls
    text = data.decode()
    assert "sdk warning" in text
    assert "our line" in text


def test_store_handler_has_no_logger_name_filter() -> None:
    assert _handler(FakeStore()).filters == []


def test_emit_can_reenter_while_the_lock_is_held() -> None:
    handler = _handler(FakeStore())
    record = logging.LogRecord("ftg_prober.x", logging.INFO, "f", 1, "m", None, None)
    with handler._lock:
        handler.emit(record)  # a signal handler that logs; a plain Lock would deadlock
    assert len(handler._buffer) == 1


def test_close_bounds_the_final_flush_and_prints_the_lost_count(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import ftg_prober.log as log_module

    release = threading.Event()

    class HungStore:
        def put(self, key: str, data: bytes) -> None:
            release.wait(5)

    monkeypatch.setattr(log_module, "CLOSE_FLUSH_SECONDS", 0.1)
    handler = _handler(HungStore())
    handler.emit(logging.LogRecord("ftg_prober.x", logging.INFO, "f", 1, "m", None, None))
    started = time.monotonic()
    handler.close()
    release.set()
    assert time.monotonic() - started < 2
    assert "1 record(s) lost" in capsys.readouterr().out


def _history_survives(handler, logger, emit_huge) -> list[dict]:
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        for i in range(5):
            logger.info("probe.error run_id=r-%d", i)
        emit_huge()
    finally:
        logger.removeHandler(handler)
    return [json.loads(line) for line, _ in handler._buffer]


def test_a_huge_plain_message_keeps_all_older_records() -> None:
    handler = _handler(FakeStore(fail=True), max_buffered_bytes=64_000)  # 1000 bytes a record
    logger = logging.getLogger("ftg_prober.test_log_plain")
    lines = _history_survives(handler, logger, lambda: logger.error("boom " + "y" * 20_000))
    assert len(lines) == 6
    assert [obj["run_id"] for obj in lines[:5]] == [f"r-{i}" for i in range(5)]
    assert lines[-1]["Body"].endswith("...[truncated]")
    assert handler._buffer[-1][1] <= 1000
    _assert_running_total(handler)


def test_a_huge_traceback_keeps_all_older_records() -> None:
    handler = _handler(FakeStore(fail=True), max_buffered_bytes=64_000)
    logger = logging.getLogger("ftg_prober.test_log_trace")

    def huge() -> None:
        try:
            raise RuntimeError("provider said: " + "z" * 20_000)
        except RuntimeError:
            logger.exception("probe.error")

    lines = _history_survives(handler, logger, huge)
    assert len(lines) == 6
    assert lines[-1]["Body"].endswith("...[truncated]")
    assert handler._buffer[-1][1] <= 1000
    _assert_running_total(handler)


def test_parse_kv_normal_cases() -> None:
    assert _parse_kv("probe.error run_id=r-1 msg='a b' q=\"c d\" empty=") == {
        "run_id": "r-1",
        "msg": "a b",
        "q": "c d",
        "empty": "",
    }
    assert _parse_kv("a=b=c x-y=1") == {"a": "b=c", "y": "1"}


_LINEAR_PROBE = (
    "from ftg_prober.log import _parse_kv\n"
    "assert _parse_kv('boom ' + 'y' * 1_000_000 + ' run_id=r-1') == {'run_id': 'r-1'}\n"
    "assert _parse_kv(\"k='\" + 'a' * 1_000_000) == {'k': \"'\" + 'a' * 1_000_000}\n"
)


def test_parse_kv_is_linear_on_a_long_word_without_an_equals_sign() -> None:
    # A quadratic scan holds the GIL for minutes, so a thread cannot time it out: use a subprocess.
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    try:
        done = subprocess.run(
            [sys.executable, "-c", _LINEAR_PROBE],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("_parse_kv took over 10 s on a 1 MB word: the scan is not linear")
    assert done.returncode == 0, done.stderr


def test_a_many_attribute_message_keeps_all_older_records_at_the_default_cap() -> None:
    handler = _handler(FakeStore(fail=True))
    logger = logging.getLogger("ftg_prober.test_log_many_attrs")
    message = "probe.error run_id=r-big " + " ".join(f"k{i}=v" for i in range(1_500_000))
    assert len(message) > 9 * 1024 * 1024
    lines = _history_survives(handler, logger, lambda: logger.error(message))
    assert len(lines) == 6
    assert [obj["run_id"] for obj in lines[:5]] == [f"r-{i}" for i in range(5)]
    assert lines[-1]["Attributes"] == {"logger": "ftg_prober.test_log_many_attrs"}
    assert lines[-1]["run_id"] is None  # a message over 1 MiB is not parsed
    assert lines[-1]["Body"].endswith("...[truncated]")
    assert handler._buffer[-1][1] <= 64 * 1024
    _assert_running_total(handler)


def test_a_message_over_the_parse_limit_is_not_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    import ftg_prober.log as log_module

    calls: list[int] = []
    monkeypatch.setattr(log_module, "_parse_kv", lambda message: calls.append(len(message)) or {})
    handler = _handler(FakeStore(fail=True))
    logger = logging.getLogger("ftg_prober.test_log_parse_limit")
    limit = log_module.MAX_PARSE_CHARS
    lines = _history_survives(
        handler,
        logger,
        lambda: (logger.error("a=b " * (limit // 4 + 1)), logger.error("c=d " * (limit // 4))),
    )
    assert [n for n in calls if n > 100] == [
        limit
    ]  # only the message at the limit; the longer one is skipped
    assert lines[-2]["Attributes"] == {"logger": "ftg_prober.test_log_parse_limit"}


def test_the_body_is_cut_by_its_escaped_length() -> None:
    handler = _handler(FakeStore(fail=True), max_buffered_bytes=64_000)  # 1000 bytes a record
    logger = logging.getLogger("ftg_prober.test_log_escaped")
    lines = _history_survives(handler, logger, lambda: logger.error("\x01" * 20_000))
    size = handler._buffer[-1][1]
    assert lines[-1]["Body"].endswith("...[truncated]")
    assert 1000 - 6 < size <= 1000  # at most 1 escaped character (6 bytes) of room is unused
    _assert_running_total(handler)


def test_experiment_provider_and_host_are_cut_only_when_still_over_the_limit() -> None:
    big = StoreLogHandler(
        FakeStore(fail=True),
        "e" * 5000,
        "p" * 5000,
        flush_seconds=0,
        max_buffered_bytes=64_000,
        host="h" * 5000,
    )
    logger = logging.getLogger("ftg_prober.test_log_fields")
    lines = _history_survives(big, logger, lambda: logger.error("boom " + "y" * 20_000))
    assert lines[-1]["host"].endswith("...[truncated]")
    assert lines[-1]["experiment"].endswith("...[truncated]")
    assert lines[-1]["Body"].endswith("...[truncated]")
    # Over the limit by the Body alone: the three fields stay whole.
    ok = StoreLogHandler(
        FakeStore(fail=True), "ftg", "aws", flush_seconds=0, max_buffered_bytes=64_000, host="h1"
    )
    lines = _history_survives(ok, logger, lambda: logger.error("boom " + "y" * 20_000))
    last = lines[-1]
    assert (last["experiment"], last["provider"], last["host"]) == ("ftg", "aws", "h1")
