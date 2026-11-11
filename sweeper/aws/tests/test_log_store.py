"""The store log handler: spec 0003 section 8.2, spec 0013 section 9."""

import json
import logging
import re
from pathlib import Path

from sweeper.log_store import S3LogHandler
from tests.fakes import put_object_ok, stubbed_s3

ROOT = Path(__file__).resolve().parent.parent
AZURE_COPY = ROOT.parent / "azure" / "sweeper" / "log_store.py"


def _shared_part(text: str) -> str:
    """Everything from the imports to the handler class: the record format, the caps, the key."""
    start = text.index("from __future__ import annotations")
    end = re.search(r"^class \w+LogHandler", text, re.MULTILINE).start()
    return text[start:end]


def test_the_record_format_code_is_the_same_as_the_azure_copy() -> None:
    """The 2 copies must not drift (decision S7): the same fields, caps, and key shape."""
    ours = (ROOT / "sweeper" / "log_store.py").read_text()
    assert _shared_part(ours) == _shared_part(AZURE_COPY.read_text())


def _logger(name: str, handler: logging.Handler) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.addHandler(handler)
    logger.propagate = False
    logger.setLevel(logging.INFO)
    return logger


def test_flush_writes_one_jsonl_object_under_the_logs_prefix() -> None:
    s3, stub, calls = stubbed_s3()
    put_object_ok(stub)
    handler = S3LogHandler(s3, "ftg-data-0123abcd", "ftg-20261015-test", host="lambda-host")
    logger = _logger("sweeper.test_flush", handler)
    try:
        logger.info("sweeper decision: action=cancelled age_s=200 region=us-east-1 id=cr-1")
        logger.info("sweeper run: scanned 1, cancelled 1, errors 0")
        with stub:
            handler.flush_to_store()
            stub.assert_no_pending_responses()
    finally:
        logger.removeHandler(handler)
    (call,) = calls
    assert call["Bucket"] == "ftg-data-0123abcd"
    assert re.fullmatch(
        r"experiment=ftg-20261015-test/logs/provider=aws/date=\d{4}-\d{2}-\d{2}/"
        r"\d{8}T\d{6}Z_lambda-host_[0-9a-f-]{36}\.jsonl",
        call["Key"],
    )
    assert "IfNoneMatch" not in call
    first, second = (json.loads(line) for line in call["Body"].decode().splitlines())
    assert first["SeverityText"] == "INFO"
    assert first["provider"] == "aws"
    assert first["experiment"] == "ftg-20261015-test"
    assert first["Attributes"]["action"] == "cancelled"
    assert first["Attributes"]["logger"] == "sweeper.test_flush"
    assert second["Body"] == "sweeper run: scanned 1, cancelled 1, errors 0"
    assert set(first) == {
        "Timestamp",
        "SeverityText",
        "Body",
        "Attributes",
        "run_id",
        "experiment",
        "provider",
        "host",
    }


def test_a_failed_flush_never_raises_and_keeps_the_records(capsys) -> None:
    s3, stub, calls = stubbed_s3()
    stub.add_client_error("put_object", service_error_code="AccessDenied", http_status_code=403)
    put_object_ok(stub)
    handler = S3LogHandler(s3, "bucket", "ftg-20261015-test")
    logger = _logger("sweeper.test_fail", handler)
    try:
        logger.info("sweeper run: scanned 0, cancelled 0, errors 0")
        with stub:
            handler.flush_to_store()  # must not raise
            assert "log flush failed, keeping 1 record(s)" in capsys.readouterr().out
            handler.flush_to_store()
            stub.assert_no_pending_responses()
    finally:
        logger.removeHandler(handler)
    assert len(calls) == 2
    assert calls[1]["Body"].decode().count("\n") == 1


def test_sdk_records_reach_the_store() -> None:
    s3, _, _ = stubbed_s3()
    handler = S3LogHandler(s3, "bucket", "ftg-20261015-test")
    record = logging.LogRecord(
        "botocore.endpoint", logging.WARNING, "", 0, "sdk warning", None, None
    )
    handler.handle(record)
    assert len(handler._buffer) == 1
    assert json.loads(handler._buffer[0][0])["Body"] == "sdk warning"


def test_no_logger_name_filter_on_the_handler() -> None:
    s3, _, _ = stubbed_s3()
    assert S3LogHandler(s3, "bucket", "ftg-20261015-test").filters == []
