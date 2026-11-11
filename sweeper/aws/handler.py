"""Lambda entry point (spec 0013 section 3). EventBridge Scheduler invokes it every minute."""

import logging
import os
from datetime import UTC, datetime
from typing import Any

import boto3
from botocore.config import Config

from sweeper import Settings, sweep
from sweeper.log_store import S3LogHandler, attach_s3_log_handler

log = logging.getLogger("sweeper")
# The decision and health lines are INFO, and the store handler must get them (section 9),
# whatever level the runtime gives the root logger (Python's own default is WARNING).
log.setLevel(logging.INFO)

# Short timeouts and the standard retry mode, so a slow Region cannot use up the function timeout
# before the log flush (section 3).
CLIENT_CONFIG = Config(
    connect_timeout=5, read_timeout=15, retries={"mode": "standard", "max_attempts": 3}
)

# Set once, at cold start. `None` means "not decided yet"; `False` means the attach failed.
_log_handler: S3LogHandler | None | bool = None


def _client(service: str, region: str | None = None) -> Any:
    return boto3.client(service, region_name=region, config=CLIENT_CONFIG)


def _attach_log_handler_once(settings: Settings) -> S3LogHandler | None:
    global _log_handler
    if _log_handler is None:
        try:
            # The function name, which Lambda sets, is the host part of the key, so the
            # sweeper's objects stand apart from the prober's under the same logs prefix.
            _log_handler = attach_s3_log_handler(
                logging.getLogger(),
                _client("s3"),
                settings.data_bucket,
                settings.experiment,
                host=os.environ.get("AWS_LAMBDA_FUNCTION_NAME"),
            )
        except Exception as exc:  # noqa: BLE001 - the sweep must run without the store log
            print(f"sweeper: store log handler not attached: {exc!r}")
            _log_handler = False
    return _log_handler or None


def lambda_handler(event: Any, context: Any) -> dict[str, int]:
    settings = Settings.from_env(os.environ)
    handler = _attach_log_handler_once(settings)
    try:
        result = sweep(
            settings,
            lambda region: _client("ec2", region),
            now=lambda: datetime.now(UTC),
            logger=log,
        )
    finally:
        # 1 flush per invocation (spec 0003 section 8.2); the handler keeps no thread.
        if handler is not None:
            handler.flush_to_store()
    return {"scanned": result.scanned, "cancelled": result.cancelled, "errors": result.errors}
