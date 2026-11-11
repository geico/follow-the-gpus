"""The sweeper's store log handler. Spec 0003 section 8.2, spec 0007 section 9."""

import json
import logging
import os
import subprocess
import sys

import pytest

from sweeper.log_store import BlobLogHandler, _parse_kv, attach_blob_log_handler, logs_key


class FakeContainer:
    """Records each `upload_blob`. `fail` makes every call raise, like a down store."""

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[dict] = []
        self.fail = fail

    def upload_blob(self, **kwargs) -> None:
        if self.fail:
            raise RuntimeError("blob store is down")
        self.calls.append(kwargs)


def test_logs_key_matches_the_prober_shape():
    from datetime import UTC, datetime

    key = logs_key("ftg", "azure", datetime(2026, 10, 14, 1, 2, 3, tzinfo=UTC), "func-host")
    assert key.startswith("experiment=ftg/logs/provider=azure/date=2026-10-14/")
    assert key.endswith(".jsonl")
    assert key.split("/")[1] == "logs"


def test_emit_buffers_and_flush_writes_one_jsonl_blob():
    container = FakeContainer()
    handler = BlobLogHandler(container, "ftg")
    logger = logging.getLogger("sweeper.test")
    logger.addHandler(handler)
    logger.propagate = False
    logger.setLevel(logging.INFO)
    try:
        logger.info(
            "sweeper decision: action=deleted age_s=200 type=vm id=x reason=older than 180 s"
        )
        handler.flush_to_store()
    finally:
        logger.removeHandler(handler)
    assert len(container.calls) == 1
    call = container.calls[0]
    assert call["name"].startswith("experiment=ftg/logs/provider=azure/date=")
    assert call["overwrite"] is False
    (line,) = call["data"].decode("utf-8").splitlines()
    obj = json.loads(line)
    assert obj["SeverityText"] == "INFO"
    assert (
        obj["Body"]
        == "sweeper decision: action=deleted age_s=200 type=vm id=x reason=older than 180 s"
    )
    assert obj["experiment"] == "ftg"
    assert obj["provider"] == "azure"
    assert obj["Attributes"]["logger"] == "sweeper.test"
    assert obj["Attributes"]["action"] == "deleted"
    assert obj["Attributes"]["age_s"] == "200"


def test_a_failed_flush_never_raises_and_keeps_the_records():
    container = FakeContainer(fail=True)
    handler = BlobLogHandler(container, "ftg")
    logger = logging.getLogger("sweeper.test_fail")
    logger.addHandler(handler)
    logger.propagate = False
    logger.setLevel(logging.INFO)
    try:
        logger.info("sweeper run: scanned 1, deleted 1, errors 0")
        handler.flush_to_store()  # must not raise
    finally:
        logger.removeHandler(handler)
    assert container.calls == []
    container.fail = False
    handler.flush_to_store()
    assert len(container.calls) == 1


def test_body_carries_the_exception_text_unfiltered():
    """Nothing in the sweeper calls `logger.exception` today, but if it did, the store copy
    must not drop the traceback (spec 0003 section 8.2)."""
    container = FakeContainer()
    handler = BlobLogHandler(container, "ftg")
    logger = logging.getLogger("sweeper.test_exc")
    logger.addHandler(handler)
    logger.propagate = False
    logger.setLevel(logging.INFO)
    try:
        try:
            raise ValueError("boom: quota exceeded")
        except ValueError:
            logger.exception("sweeper query failed")
        handler.flush_to_store()
    finally:
        logger.removeHandler(handler)
    (line,) = [json.loads(x) for x in container.calls[0]["data"].decode("utf-8").splitlines()]
    assert "boom: quota exceeded" in line["Body"]
    assert "Traceback (most recent call last)" in line["Body"]


def test_the_buffer_is_capped():
    container = FakeContainer(fail=True)
    handler = BlobLogHandler(container, "ftg", max_buffered=2)
    logger = logging.getLogger("sweeper.test_cap")
    logger.addHandler(handler)
    logger.propagate = False
    logger.setLevel(logging.INFO)
    try:
        for i in range(4):
            logger.info("sweeper run: scanned %d, deleted 0, errors 0", i)
        handler.flush_to_store()
    finally:
        logger.removeHandler(handler)
    assert len(handler._buffer) == 2


def _assert_running_total(handler):
    sizes = [len(line.encode()) + 1 for line, _ in handler._buffer]
    assert [size for _, size in handler._buffer] == sizes
    assert handler._buffer_bytes == sum(sizes)


def test_one_record_over_the_byte_cap_is_truncated_and_older_records_stay():
    handler = BlobLogHandler(FakeContainer(fail=True), "ftg", max_buffered_bytes=1_000_000)
    logger = logging.getLogger("sweeper.test_huge")
    logger.addHandler(handler)
    logger.propagate = False
    logger.setLevel(logging.INFO)
    try:
        for i in range(5):
            logger.info("sweeper run_id=r-%d", i)
        logger.info("sweeper run_id=r-big detail=%s", "y" * 20_000)
    finally:
        logger.removeHandler(handler)
    lines = [json.loads(line) for line, _ in handler._buffer]
    assert [obj["run_id"] for obj in lines] == ["r-0", "r-1", "r-2", "r-3", "r-4", "r-big"]
    assert lines[-1]["Body"].endswith("...[truncated]")
    _assert_running_total(handler)


def test_the_running_total_matches_the_line_sizes_after_drops_flushes_and_a_failed_flush():
    container = FakeContainer(fail=True)
    handler = BlobLogHandler(container, "ftg", max_buffered=4, max_buffered_bytes=64_000)
    logger = logging.getLogger("sweeper.test_total")
    logger.addHandler(handler)
    logger.propagate = False
    logger.setLevel(logging.INFO)
    try:
        for i in range(8):
            logger.info("sweeper run_id=r-%d detail=%s", i, "z" * 300)
            _assert_running_total(handler)
        handler.flush_to_store()  # fails: the batch goes back
        _assert_running_total(handler)
        logger.info("sweeper run_id=r-8 detail=%s", "z" * 300)
        _assert_running_total(handler)
        container.fail = False
        handler.flush_to_store()
        assert handler._buffer_bytes == 0
        assert not handler._buffer
    finally:
        logger.removeHandler(handler)


def test_the_buffer_is_capped_in_bytes_and_drops_the_oldest_first():
    container = FakeContainer(fail=True)
    handler = BlobLogHandler(container, "ftg", max_buffered_bytes=64_000)
    logger = logging.getLogger("sweeper.test_byte_cap")
    logger.addHandler(handler)
    logger.propagate = False
    logger.setLevel(logging.INFO)
    try:
        for i in range(100):
            logger.info("sweeper run_id=r-%d detail=%s", i, "x" * 800)
        handler.flush_to_store()  # fails, keeps the batch under the cap
        logger.info("sweeper run_id=r-100 detail=%s", "x" * 800)
    finally:
        logger.removeHandler(handler)
    _assert_running_total(handler)
    sizes = [size for _, size in handler._buffer]
    assert 0 < len(sizes) < 100
    assert sum(sizes) <= 64_000
    assert json.loads(handler._buffer[-1][0])["run_id"] == "r-100"
    assert json.loads(handler._buffer[0][0])["run_id"] != "r-0"


def test_attach_blob_log_handler_adds_it_to_the_given_logger():
    container = FakeContainer()
    logger = logging.getLogger("sweeper.test_attach")
    handler = attach_blob_log_handler(logger, container, "ftg")
    try:
        assert handler in logger.handlers
    finally:
        logger.removeHandler(handler)


def test_sdk_records_reach_the_handler_and_kv_fields_are_parsed():
    container = FakeContainer()
    handler = BlobLogHandler(container, "ftg")
    root = logging.getLogger()
    root.addHandler(handler)
    old_level = root.level
    root.setLevel(logging.DEBUG)
    try:
        logging.getLogger("azure.core").warning("sdk warning")
        logging.getLogger("sweeper.run").info("sweeper decision: action=deleted run_id=r1 id='a b'")
        handler.flush_to_store()
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)
    ((call,),) = [(c,) for c in container.calls]
    text = call["data"].decode()
    sdk_line, line = text.splitlines()
    assert json.loads(sdk_line)["Body"] == "sdk warning"
    obj = json.loads(line)
    assert obj["run_id"] == "r1"
    assert obj["Attributes"]["id"] == "a b"
    assert obj["Attributes"]["logger"] == "sweeper.run"


def _history_survives(handler, logger, emit_huge):
    logger.addHandler(handler)
    logger.propagate = False
    logger.setLevel(logging.INFO)
    try:
        for i in range(5):
            logger.info("sweeper run_id=r-%d", i)
        emit_huge()
    finally:
        logger.removeHandler(handler)
    return [json.loads(line) for line, _ in handler._buffer]


def test_a_huge_plain_message_keeps_all_older_records():
    handler = BlobLogHandler(FakeContainer(fail=True), "ftg", max_buffered_bytes=64_000)
    logger = logging.getLogger("sweeper.test_plain")
    lines = _history_survives(handler, logger, lambda: logger.error("boom " + "y" * 20_000))
    assert len(lines) == 6
    assert [obj["run_id"] for obj in lines[:5]] == [f"r-{i}" for i in range(5)]
    assert lines[-1]["Body"].endswith("...[truncated]")
    assert handler._buffer[-1][1] <= 1000
    _assert_running_total(handler)


def test_a_huge_traceback_keeps_all_older_records():
    handler = BlobLogHandler(FakeContainer(fail=True), "ftg", max_buffered_bytes=64_000)
    logger = logging.getLogger("sweeper.test_trace")

    def huge():
        try:
            raise RuntimeError("provider said: " + "z" * 20_000)
        except RuntimeError:
            logger.exception("sweep failed")

    lines = _history_survives(handler, logger, huge)
    assert len(lines) == 6
    assert lines[-1]["Body"].endswith("...[truncated]")
    assert handler._buffer[-1][1] <= 1000
    _assert_running_total(handler)


def test_parse_kv_normal_cases():
    assert _parse_kv("sweeper run_id=r-1 msg='a b' q=\"c d\" empty=") == {
        "run_id": "r-1",
        "msg": "a b",
        "q": "c d",
        "empty": "",
    }
    assert _parse_kv("a=b=c x-y=1") == {"a": "b=c", "y": "1"}


_LINEAR_PROBE = (
    "from sweeper.log_store import _parse_kv\n"
    "assert _parse_kv('boom ' + 'y' * 1_000_000 + ' run_id=r-1') == {'run_id': 'r-1'}\n"
    "assert _parse_kv(\"k='\" + 'a' * 1_000_000) == {'k': \"'\" + 'a' * 1_000_000}\n"
)


def test_parse_kv_is_linear_on_a_long_word_without_an_equals_sign():
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


def test_a_many_attribute_message_keeps_all_older_records_at_the_default_cap():
    handler = BlobLogHandler(FakeContainer(fail=True), "ftg")
    logger = logging.getLogger("sweeper.test_many_attrs")
    message = "sweeper run_id=r-big " + " ".join(f"k{i}=v" for i in range(1_500_000))
    assert len(message) > 9 * 1024 * 1024
    lines = _history_survives(handler, logger, lambda: logger.error(message))
    assert len(lines) == 6
    assert [obj["run_id"] for obj in lines[:5]] == [f"r-{i}" for i in range(5)]
    assert lines[-1]["Attributes"] == {"logger": "sweeper.test_many_attrs"}
    assert lines[-1]["run_id"] is None  # a message over 1 MiB is not parsed
    assert lines[-1]["Body"].endswith("...[truncated]")
    assert handler._buffer[-1][1] <= 64 * 1024
    _assert_running_total(handler)


def test_a_message_over_the_parse_limit_is_not_parsed(monkeypatch):
    import sweeper.log_store as log_module

    calls = []
    monkeypatch.setattr(log_module, "_parse_kv", lambda message: calls.append(len(message)) or {})
    handler = BlobLogHandler(FakeContainer(fail=True), "ftg")
    logger = logging.getLogger("sweeper.test_parse_limit")
    limit = log_module.MAX_PARSE_CHARS
    lines = _history_survives(
        handler,
        logger,
        lambda: (logger.error("a=b " * (limit // 4 + 1)), logger.error("c=d " * (limit // 4))),
    )
    assert [n for n in calls if n > 100] == [
        limit
    ]  # only the message at the limit; the longer one is skipped
    assert lines[-2]["Attributes"] == {"logger": "sweeper.test_parse_limit"}


def test_the_body_is_cut_by_its_escaped_length():
    handler = BlobLogHandler(FakeContainer(fail=True), "ftg", max_buffered_bytes=64_000)
    logger = logging.getLogger("sweeper.test_escaped")
    lines = _history_survives(handler, logger, lambda: logger.error("\x01" * 20_000))
    size = handler._buffer[-1][1]
    assert lines[-1]["Body"].endswith("...[truncated]")
    assert 1000 - 6 < size <= 1000  # at most 1 escaped character (6 bytes) of room is unused
    _assert_running_total(handler)


def test_experiment_provider_and_host_are_cut_only_when_still_over_the_limit():
    logger = logging.getLogger("sweeper.test_fields")
    big = BlobLogHandler(
        FakeContainer(fail=True),
        "e" * 5000,
        provider="p" * 5000,
        max_buffered_bytes=64_000,
        host="h" * 5000,
    )
    lines = _history_survives(big, logger, lambda: logger.error("boom " + "y" * 20_000))
    assert lines[-1]["host"].endswith("...[truncated]")
    assert lines[-1]["experiment"].endswith("...[truncated]")
    assert lines[-1]["Body"].endswith("...[truncated]")
    ok = BlobLogHandler(FakeContainer(fail=True), "ftg", max_buffered_bytes=64_000, host="h1")
    lines = _history_survives(ok, logger, lambda: logger.error("boom " + "y" * 20_000))
    assert (lines[-1]["experiment"], lines[-1]["provider"], lines[-1]["host"]) == (
        "ftg",
        "azure",
        "h1",
    )


def test_no_logger_name_filter_on_the_handler():
    assert BlobLogHandler(FakeContainer(), "ftg").filters == []
