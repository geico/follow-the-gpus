"""Azure Spot Placement Score probe. Spec 0009 section 2, spec 0002 section 4.6."""

import logging
import math
import re
import time
from collections.abc import Callable
from typing import Any

from ftg_prober.log import kv, log_provider_error
from ftg_prober.probes import Probe, ProbeContext, azure_subscription_id
from ftg_prober.record import Record

log = logging.getLogger(__name__)

API_VERSION = "2025-06-05"
ARM_SCOPE = "https://management.azure.com/.default"
ENDPOINT = (
    "https://management.azure.com/subscriptions/{subscription}/providers/Microsoft.Compute"
    "/locations/{location}/placementScores/spot/generate?api-version={api_version}"
)
SIZES_PER_REQUEST = 5
REGIONS_PER_REQUEST = 8
DESIRED_COUNT = 1
RETRY_WAIT_S = 60
HTTP_TIMEOUT_S = 30
# A zonal row whose request failed has no known zone. Spec 0009 section 2.6.
UNKNOWN_ZONE = "unknown"
# The `error_code` of a 200 response whose body is not a JSON object.
INVALID_RESPONSE = "InvalidResponse"
# The `error_code` of a row for a request that the probe did not send, because a long 429 wait
# is still running. Spec 0009 section 2.6.
RATE_LIMITED = "RateLimited"
_WAIT_IN_TEXT = re.compile(r"try again after\s+(\d+)\s+seconds?", re.IGNORECASE)

# Spec 0002 section 4.6: the score value to the `outcome` of the row.
OUTCOME_BY_SCORE = {
    "High": "success",
    "Medium": "success",
    "Low": "success",
    "RestrictedSkuNotAvailable": "restricted",
    "InsufficientQuota": "quota",
    "DataNotFoundOrStale": "none",
}


def score_outcome(score: Any) -> str:
    """The `outcome` of a score value. Any value that is not in the map is `error`."""
    return OUTCOME_BY_SCORE.get(score, "error") if isinstance(score, str) else "error"


def chunks(items: list[Any], size: int) -> list[list[Any]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def request_body(regions: list[str], sizes: list[str], zonal: bool) -> dict[str, Any]:
    return {
        "desiredLocations": list(regions),
        "desiredSizes": [{"sku": size} for size in sizes],
        "desiredCount": DESIRED_COUNT,
        "availabilityZones": zonal,
    }


def _default_post(url: str, headers: dict[str, str], body: dict[str, Any]) -> Any:
    import requests

    return requests.post(url, headers=headers, json=body, timeout=HTTP_TIMEOUT_S)


def _error_code(response: Any) -> str:
    """The ARM error code of a failed response, else `HTTP <status>`."""
    try:
        code = (response.json() or {}).get("error", {}).get("code")
    except Exception:  # noqa: BLE001 - a body that is not JSON has no code
        code = None
    return str(code) if code else f"HTTP {response.status_code}"


def _error_message(response: Any) -> str:
    """The ARM error message of a failed response as returned, else the body text."""
    try:
        message = (response.json() or {}).get("error", {}).get("message")
    except Exception:  # noqa: BLE001 - a body that is not JSON has no message
        message = None
    return str(message) if message else str(getattr(response, "text", "") or "")


def _retry_after_s(response: Any) -> int | None:
    """The wait that a 429 asks for, in seconds, else None.

    The `Retry-After` header (a whole number of seconds) wins. Else the text of the body, JSON
    or plain: "try again after N seconds". An HTTP date or any other form gives None.
    """
    try:
        value = str((getattr(response, "headers", None) or {}).get("Retry-After", "")).strip()
        if value.isdigit():
            return int(value)
        match = _WAIT_IN_TEXT.search(str(getattr(response, "text", "") or ""))
        return int(match.group(1)) if match else None
    except Exception:  # noqa: BLE001 - an unreadable response asks for no wait
        return None


class SpotScoreProbe(Probe):
    """Regional scores for each (size, region), plus 1 zonal request per region."""

    probe_type = "az_spot_score"
    provider = "azure"
    default_cadence = "15m"

    def __init__(
        self,
        post: Callable[[str, dict[str, str], dict[str, Any]], Any] | None = None,
        sleep: Callable[[float], None] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.post = post or _default_post
        self.sleep = sleep or time.sleep
        self.clock = clock or time.monotonic
        # The `clock` time at which a long 429 wait ends. The state lives as long as the probe.
        self._paused_until: float | None = None

    def run(self, ctx: ProbeContext) -> list[Record]:
        sizes = list(ctx.config["spot_sizes"])
        regions = list(ctx.config["regions"])
        subscription = azure_subscription_id()
        credential = ctx.clients.azure("credential")
        rows: list[Record] = []
        for size_group in chunks(sizes, SIZES_PER_REQUEST):
            for region_group in chunks(regions, REGIONS_PER_REQUEST):
                rows.extend(
                    self._request(ctx, credential, subscription, region_group, size_group, False)
                )
        for region in regions:
            for size_group in chunks(sizes, SIZES_PER_REQUEST):
                rows.extend(
                    self._request(ctx, credential, subscription, [region], size_group, True)
                )
        return rows

    def _request(
        self,
        ctx: ProbeContext,
        credential: Any,
        subscription: str,
        regions: list[str],
        sizes: list[str],
        zonal: bool,
    ) -> list[Record]:
        """1 request, with 1 retry after a 429. Return the rows of the request."""
        url = ENDPOINT.format(
            subscription=subscription, location=regions[0], api_version=API_VERSION
        )
        body = request_body(regions, sizes, zonal)
        started = self.clock()
        if self._paused_until is not None and started < self._paused_until:
            remaining = math.ceil(self._paused_until - started)
            return self._error_rows(
                ctx, regions, sizes, zonal, RATE_LIMITED, 0, {"retry_after_s": remaining}
            )
        error_code: str | None = None
        error_message: str | None = None
        http_status: int | None = None
        payload: dict[str, Any] | None = None
        extra: dict[str, Any] = {}
        for attempt in (1, 2):
            try:
                token = credential.get_token(ARM_SCOPE).token
                response = self.post(
                    url,
                    {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    body,
                )
            except Exception as exc:  # noqa: BLE001 - a failed call becomes error rows
                error_code, http_status, error_message = type(exc).__name__, None, str(exc)
                self._log_failure(regions, zonal, error_code, http_status, error_message)
                break
            http_status = response.status_code
            if response.status_code == 200:
                try:
                    payload = response.json()
                except ValueError:
                    payload = None
                if isinstance(payload, dict):
                    error_code = None
                else:
                    payload, error_code = None, INVALID_RESPONSE
                    error_message = str(getattr(response, "text", "") or "")
                    self._log_failure(regions, zonal, error_code, http_status, error_message)
                break
            error_code = _error_code(response)
            error_message = _error_message(response)
            self._log_failure(regions, zonal, error_code, http_status, error_message)
            if response.status_code == 429:
                wait = _retry_after_s(response)
                if wait is not None and wait > RETRY_WAIT_S:
                    self._paused_until = self.clock() + wait
                    extra = {"retry_after_s": wait}
                    log.warning(
                        "az_spot_score.paused %s",
                        kv(location=regions[0], zonal=zonal, retry_after_s=wait),
                    )
                    break
            if response.status_code == 429 and attempt == 1:
                log.warning(
                    "az_spot_score.throttled %s",
                    kv(location=regions[0], zonal=zonal, wait_s=RETRY_WAIT_S),
                )
                self.sleep(RETRY_WAIT_S)
                continue
            break
        latency_ms = int((self.clock() - started) * 1000)
        if payload is None:
            if http_status is not None:
                extra = {"http_status": http_status} | extra
            return self._error_rows(ctx, regions, sizes, zonal, error_code, latency_ms, extra)
        return self._score_rows(ctx, payload, regions, sizes, zonal, latency_ms)

    def _log_failure(
        self,
        regions: list[str],
        zonal: bool,
        code: str | None,
        status: int | None,
        message: str | None,
    ) -> None:
        """1 log line for 1 failed call, with the message as Azure returned it."""
        log_provider_error(
            log,
            probe_type=self.probe_type,
            region=",".join(regions),
            code=code,
            status=status,
            message=message,
            event="az_spot_score.failed",
            zonal=zonal,
        )

    def _error_rows(
        self,
        ctx: ProbeContext,
        regions: list[str],
        sizes: list[str],
        zonal: bool,
        error_code: str | None,
        latency_ms: int,
        extra: dict[str, Any],
    ) -> list[Record]:
        """1 `error` row per (size, region) pair of a request that gave no score."""
        details = {
            "score": None,
            "desired_count": DESIRED_COUNT,
            "scope": "zonal" if zonal else "regional",
        } | extra
        zone = UNKNOWN_ZONE if zonal else None
        return [
            self._row(ctx, region, zone, size, "error", error_code, latency_ms, dict(details))
            for region in regions
            for size in sizes
        ]

    def _score_rows(
        self,
        ctx: ProbeContext,
        payload: dict[str, Any],
        regions: list[str],
        sizes: list[str],
        zonal: bool,
        latency_ms: int,
    ) -> list[Record]:
        scope = "zonal" if zonal else "regional"
        items: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for item in payload.get("placementScores") or []:
            key = (str(item.get("sku", "")).lower(), str(item.get("region", "")).lower())
            items.setdefault(key, []).append(item)
        rows = []
        for region in regions:
            for size in sizes:
                found = items.get((size.lower(), region.lower()), [])
                if not found and not zonal:
                    rows.append(
                        self._row(
                            ctx,
                            region,
                            None,
                            size,
                            "none",
                            None,
                            latency_ms,
                            {"score": None, "desired_count": DESIRED_COUNT, "scope": scope},
                        )
                    )
                    continue
                if not zonal:
                    found = found[:1]
                for item in found:
                    score = item.get("score")
                    outcome = score_outcome(score)
                    error_code = str(score) if outcome in ("restricted", "quota", "error") else None
                    zone = item.get("availabilityZone") if zonal else None
                    rows.append(
                        self._row(
                            ctx,
                            region,
                            str(zone) if zone is not None else None,
                            size,
                            outcome,
                            error_code,
                            latency_ms,
                            {
                                "score": score,
                                "desired_count": DESIRED_COUNT,
                                "is_quota_available": item.get("isQuotaAvailable"),
                                "scope": scope,
                            },
                        )
                    )
        return rows

    def _row(
        self,
        ctx: ProbeContext,
        region: str,
        zone: str | None,
        size: str,
        outcome: str,
        error_code: str | None,
        latency_ms: int,
        details: dict[str, Any],
    ) -> Record:
        return Record(
            ts_utc=ctx.timestamp(),
            run_id=ctx.run_id,
            simulated=ctx.simulate,
            provider=self.provider,
            region=region,
            zone=zone,
            probe_type=self.probe_type,
            sku=size,
            placement_group_mode="n/a",
            n_requested=DESIRED_COUNT,
            n_placed=None,
            outcome=outcome,
            error_code=error_code,
            latency_ms=latency_ms,
            details=details,
        )
