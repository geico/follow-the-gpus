"""Helpers for the AWS probes. Spec 0008 section 2. This module defines no probe."""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ftg_prober.log import log_provider_error
from ftg_prober.probes import ProbeContext
from ftg_prober.record import Record

log = logging.getLogger(__name__)

# An instance type is a GPU type when its family starts with 1 of these letters.
GPU_FAMILY_LETTERS = ("p", "g")


def family(instance_type: str) -> str:
    """The family part of an instance type: `p5` for `p5.48xlarge`."""
    return instance_type.split(".", 1)[0]


def is_gpu_type(instance_type: str) -> bool:
    return family(instance_type)[:1] in GPU_FAMILY_LETTERS


def request_gpu_types(section: dict[str, Any]) -> list[str]:
    """Each GPU instance type of the Spot score requests, 1 time, in request order."""
    types: list[str] = []
    for entry in section.get("spot_score_requests") or []:
        for instance_type in entry["instance_types"]:
            if is_gpu_type(instance_type) and instance_type not in types:
                types.append(instance_type)
    return types


def error_code(exc: BaseException) -> str:
    """The raw AWS error code of a botocore `ClientError`, else the exception class name."""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = (response.get("Error") or {}).get("Code")
        if code:
            return str(code)
    return type(exc).__name__


def error_message(exc: BaseException) -> str:
    """The message of a botocore `ClientError` as AWS returned it, else `str(exc)`."""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        message = (response.get("Error") or {}).get("Message")
        if message:
            return str(message)
    return str(exc)


def error_status(exc: BaseException) -> int | None:
    """The HTTP status of a botocore `ClientError`, else None."""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        status = (response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
        return int(status) if isinstance(status, int) else None
    return None


def log_aws_error(
    logger: logging.Logger, probe_type: str, region: str | None, exc: BaseException, **fields: Any
) -> None:
    """1 log line for 1 failed AWS call: probe type, Region, code, status, and the message."""
    log_provider_error(
        logger,
        probe_type=probe_type,
        region=region,
        code=error_code(exc),
        status=error_status(exc),
        message=error_message(exc),
        **fields,
    )


@dataclass
class ApiResult:
    """The items of 1 API call with all its pages, the latency, and the error code if any."""

    items: list[dict[str, Any]] = field(default_factory=list)
    latency_ms: int = 0
    error_code: str | None = None
    pages: int = 0


def paginate(
    operation: Callable[..., dict[str, Any]],
    kwargs: dict[str, Any],
    list_key: str,
    clock: Callable[[], float] = time.monotonic,
    *,
    probe_type: str,
    region: str | None,
) -> ApiResult:
    """Call `operation` and follow `NextToken` until it is empty. Never raise.

    The latency is the sum over the pages. An exception ends the call, sets `error_code`, and
    logs 1 line with the message as AWS returned it (the row never holds it).
    """
    result = ApiResult()
    token: str | None = None
    while True:
        args = dict(kwargs)
        if token:
            args["NextToken"] = token
        started = clock()
        try:
            response = operation(**args)
        except Exception as exc:  # noqa: BLE001 - each failure becomes an error row
            result.latency_ms += _ms(clock() - started)
            result.error_code = error_code(exc)
            log_aws_error(log, probe_type, region, exc)
            return result
        result.latency_ms += _ms(clock() - started)
        result.pages += 1
        result.items.extend(response.get(list_key) or [])
        token = response.get("NextToken")
        if not token:
            return result


def _ms(seconds: float) -> int:
    return max(0, int(round(seconds * 1000)))


def row(
    ctx: ProbeContext,
    probe_type: str,
    *,
    region: str,
    sku: str,
    outcome: str,
    latency_ms: int,
    zone: str | None = None,
    n_requested: int = 1,
    n_placed: int | None = None,
    error_code: str | None = None,
    details: dict[str, Any] | None = None,
) -> Record:
    """One AWS row with the shared fields of spec 0003 section 4.1."""
    return Record(
        ts_utc=ctx.timestamp(),
        run_id=ctx.run_id,
        simulated=ctx.simulate,
        provider="aws",
        region=region,
        zone=zone,
        probe_type=probe_type,
        sku=sku,
        placement_group_mode="n/a",
        n_requested=n_requested,
        n_placed=n_placed,
        outcome=outcome,
        error_code=error_code,
        latency_ms=latency_ms,
        details=details or {},
    )


def to_float(value: Any) -> float | None:
    """A number from an AWS string such as `"31.464"`, or None."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
