"""Azure Spot eviction rate and Spot price from Resource Graph. Spec 0009 section 3."""

import logging
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from ftg_prober.log import log_provider_error
from ftg_prober.probes import Probe, ProbeContext
from ftg_prober.record import Record

log = logging.getLogger(__name__)

OS_TYPE = "linux"
PAGE_SIZE = 1000
# A price with an effective date older than this is no signal (spec 0009, 3.3 and 3.5).
MAX_AGE_KEY = "spot_price_max_age_days"
DEFAULT_PRICE_MAX_AGE_DAYS = 540
# Sizes and regions go into the query text. Only these characters are allowed.
SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")

EVICTION_QUERY = """SpotResources
| where type =~ 'microsoft.compute/skuspotevictionrate/location'
| where sku.name in~ ({sizes})
| where location in~ ({regions})
| project skuName = tostring(sku.name), location, spotEvictionRate = tostring(properties.evictionRate)"""  # noqa: E501

PRICE_QUERY = """SpotResources
| where type =~ 'microsoft.compute/skuspotpricehistory/ostype/location'
| where sku.name in~ ({sizes})
| where properties.osType =~ '{os_type}'
| where location in~ ({regions})
| project skuName = tostring(sku.name), osType = tostring(properties.osType), location, latestSpotPriceUSD = todouble(properties.spotPrices[0].priceUSD), latestSpotPriceEffectiveDate = tostring(properties.spotPrices[0].effectiveDate)"""  # noqa: E501

# An eviction rate range as Resource Graph returns it: `5-10` or the open range `20+`.
CLOSED_RANGE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*$")
OPEN_RANGE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*\+\s*$")


def kql_list(names: list[str]) -> str:
    """`'a', 'b'` for a KQL `in~` list. Refuse a name with a character outside SAFE_NAME."""
    for name in names:
        if not SAFE_NAME.match(name):
            raise ValueError(f"name {name!r} has a character outside [A-Za-z0-9_.-]")
    return ", ".join(f"'{name}'" for name in names)


def eviction_query(sizes: list[str], regions: list[str]) -> str:
    return EVICTION_QUERY.format(sizes=kql_list(sizes), regions=kql_list(regions))


def price_query(sizes: list[str], regions: list[str]) -> str:
    return PRICE_QUERY.format(sizes=kql_list(sizes), regions=kql_list(regions), os_type=OS_TYPE)


def sdk_query_request(query: str, skip_token: str | None) -> Any:
    """The SDK `QueryRequest` of 1 page, at tenant scope.

    The request has no `subscriptions`: a subscription-scope query of `SpotResources`
    returned 0 rows in the live check of 2026-10-12. See spec 0009, 3.2.
    """
    from azure.mgmt.resourcegraph.models import QueryRequest, QueryRequestOptions

    return QueryRequest(
        query=query,
        options=QueryRequestOptions(
            top=PAGE_SIZE, skip_token=skip_token, result_format="objectArray"
        ),
    )


def _error_code(exc: Exception) -> str:
    """The provider error code of an SDK exception, else the exception class name."""
    error = getattr(exc, "error", None)
    code = getattr(error, "code", None)
    return str(code) if code else type(exc).__name__


def _error_message(exc: Exception) -> str:
    """The provider message of an SDK exception as returned, else `str(exc)`."""
    message = getattr(getattr(exc, "error", None), "message", None)
    return str(message) if message else str(exc)


def _pct(text: str) -> int | float:
    number = float(text)
    return int(number) if number.is_integer() else number


def eviction_bounds(rate: str | None) -> tuple[int | float | None, int | float | None]:
    """The lower and upper bound in percent of a range such as `5-10` or `20+`.

    An open bound is None. A value that is not a range gives (None, None).
    """
    if rate is None:
        return None, None
    closed = CLOSED_RANGE.match(rate)
    if closed:
        return _pct(closed.group(1)), _pct(closed.group(2))
    open_range = OPEN_RANGE.match(rate)
    if open_range:
        return _pct(open_range.group(1)), None
    return None, None


def _text(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None  # NaN is no value


def price_max_age_days(section: dict[str, Any]) -> int:
    """`spot_price_max_age_days` of the `azure` section. It must be an integer of 1 or more."""
    value = section.get(MAX_AGE_KEY, DEFAULT_PRICE_MAX_AGE_DAYS)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"azure.{MAX_AGE_KEY} must be an integer of 1 or more, not {value!r}")
    return value


def _parse_date(text: str | None) -> datetime | None:
    """An ISO 8601 date as a UTC datetime. A date with no time zone is UTC. Else None."""
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def price_is_signal(
    price: float | None, effective_date: str | None, now: datetime, max_age_days: int
) -> bool:
    """False for a price of 0 or less, or a price older than `max_age_days`. Spec 0009, 3.3."""
    if price is not None and price <= 0:
        return False
    effective = _parse_date(effective_date)
    return effective is None or now - effective <= timedelta(days=max_age_days)


class SpotResourceGraphProbe(Probe):
    """1 row per (size, region) with the eviction rate bucket and the latest Spot price."""

    probe_type = "az_spot"
    provider = "azure"
    default_cadence = "5m"

    def __init__(
        self,
        make_request: Callable[[str, str | None], Any] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.make_request = make_request or sdk_query_request
        self.clock = clock or time.monotonic

    def run(self, ctx: ProbeContext) -> list[Record]:
        sizes = list(ctx.config["spot_sizes"])
        regions = list(ctx.config["regions"])
        max_age_days = price_max_age_days(ctx.config)
        client = ctx.clients.azure("resourcegraph")
        started = self.clock()
        evictions: dict[tuple[str, str], dict[str, Any]] = {}
        prices: dict[tuple[str, str], dict[str, Any]] = {}
        errors: dict[str, str] = {}
        for name, query, target in (
            ("eviction", eviction_query(sizes, regions), evictions),
            ("price", price_query(sizes, regions), prices),
        ):
            try:
                for item in self._query(client, query):
                    key = (
                        str(item.get("skuName", "")).lower(),
                        str(item.get("location", "")).lower(),
                    )
                    target.setdefault(key, item)
            except Exception as exc:  # noqa: BLE001 - a failed query becomes error rows
                errors[name] = _error_code(exc)
                log_provider_error(
                    log,
                    probe_type=self.probe_type,
                    region=",".join(regions),
                    code=errors[name],
                    status=getattr(exc, "status_code", None),
                    message=_error_message(exc),
                    event="az_spot.query_failed",
                    query=name,
                )
        latency_ms = int((self.clock() - started) * 1000)
        rows = []
        for region in regions:
            for size in sizes:
                key = (size.lower(), region.lower())
                eviction = evictions.get(key)
                price = prices.get(key)
                rate = _text(eviction.get("spotEvictionRate")) if eviction else None
                rate_min, rate_max = eviction_bounds(rate)
                details = {
                    "eviction_rate": rate,
                    "eviction_rate_min_pct": rate_min,
                    "eviction_rate_max_pct": rate_max,
                    "price": _number(price.get("latestSpotPriceUSD")) if price else None,
                    "price_effective_date": (
                        _text(price.get("latestSpotPriceEffectiveDate")) if price else None
                    ),
                    "currency": "USD",
                    "os_type": str(price.get("osType") or OS_TYPE) if price else OS_TYPE,
                    "eviction_error_code": errors.get("eviction"),
                    "price_error_code": errors.get("price"),
                }
                no_signal = (eviction is None and price is None) or (
                    price is not None
                    and not price_is_signal(
                        details["price"], details["price_effective_date"], ctx.now(), max_age_days
                    )
                )
                if errors:
                    outcome, error_code = "error", next(iter(errors.values()))
                elif no_signal:
                    outcome, error_code = "none", None
                else:
                    outcome, error_code = "success", None
                rows.append(
                    Record(
                        ts_utc=ctx.timestamp(),
                        run_id=ctx.run_id,
                        simulated=ctx.simulate,
                        provider=self.provider,
                        region=region,
                        zone=None,
                        probe_type=self.probe_type,
                        sku=size,
                        placement_group_mode="n/a",
                        n_requested=1,
                        n_placed=None,
                        outcome=outcome,
                        error_code=error_code,
                        latency_ms=latency_ms,
                        details=details,
                    )
                )
        return rows

    def _query(self, client: Any, query: str) -> list[dict[str, Any]]:
        """All pages of 1 query, as a list of objects."""
        items: list[dict[str, Any]] = []
        skip_token: str | None = None
        while True:
            response = client.resources(self.make_request(query, skip_token))
            data = getattr(response, "data", None) or []
            if not isinstance(data, list):
                raise TypeError("Resource Graph data is not an object array")
            items.extend(item for item in data if isinstance(item, dict))
            skip_token = getattr(response, "skip_token", None)
            if not skip_token:
                return items
