"""Azure Functions entry point (Python v2 model). The timer runs the sweeper every minute."""

import functools
import logging
import os
from datetime import UTC, datetime

import azure.functions as func

from sweeper import Settings, sweep
from sweeper.azure_clients import build_clients
from sweeper.code_sha import code_sha
from sweeper.log_store import BlobLogHandler, attach_blob_log_handler

app = func.FunctionApp()
log = logging.getLogger("sweeper")

# Set once, at cold start, from the first invocation whose settings name a data storage
# account. `None` means "not decided yet"; `False` means "no account is set, stay off".
_log_handler: BlobLogHandler | None | bool = None


def _attach_log_handler_once(settings: Settings) -> BlobLogHandler | None:
    global _log_handler
    if _log_handler is None:
        if not settings.data_storage_account:
            _log_handler = False
        else:
            try:
                from azure.identity import DefaultAzureCredential
                from azure.storage.blob import ContainerClient

                container = ContainerClient(
                    account_url=f"https://{settings.data_storage_account}.blob.core.windows.net",
                    container_name="data",
                    credential=DefaultAzureCredential(),
                )
                _log_handler = attach_blob_log_handler(
                    logging.getLogger(), container, settings.experiment
                )
            except Exception as exc:  # noqa: BLE001 - the sweep must run without the store log
                print(f"sweeper: store log handler not attached: {exc!r}")
                _log_handler = False
    return _log_handler or None


@functools.cache
def _code_sha() -> str:
    return code_sha()


@app.timer_trigger(schedule="0 */1 * * * *", arg_name="timer", run_on_startup=False)
def sweep_probe_resources(timer: func.TimerRequest) -> None:
    if timer.past_due:
        log.info("sweeper timer: past due")
    settings = Settings.from_env(os.environ)
    handler = _attach_log_handler_once(settings)
    try:
        # The post-apply check compares this value with the sweeper_code_sha output.
        log.info("sweeper code_sha=%s", _code_sha())
        try:
            rows, deleter, scaler = build_clients(os.environ)
        except Exception as exc:
            # A missing AZURE_SUBSCRIPTION_ID lands here. The log reaches the store in the finally.
            log.error("sweeper clients not built: %s", exc)
            raise
        sweep(settings, rows, deleter, scaler, now=lambda: datetime.now(UTC), logger=log)
    finally:
        # The timer already runs every minute, so 1 flush per invocation is the schedule
        # (spec 0003 section 8.2); the handler keeps no background thread of its own.
        if handler is not None:
            handler.flush_to_store()
