"""Tests of the Azure Resource Graph Spot probe. Fakes only: no network, no credential."""

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from ftg_prober.azure.spot_resource_graph import (
    DEFAULT_PRICE_MAX_AGE_DAYS,
    SpotResourceGraphProbe,
    eviction_bounds,
    eviction_query,
    kql_list,
    price_is_signal,
    price_max_age_days,
    price_query,
)
from ftg_prober.probes import Clients, ProbeContext, load_probes, packages_for
from tests.conftest import import_or_skip

SUBSCRIPTION = "test-subscription"
SIZES = ["Standard_A", "Standard_B"]
REGIONS = ["eastus", "westus"]
FIXTURES = Path(__file__).parent / "fixtures"
# The azure targets of config/targets.yaml on 2026-10-12, when the fixtures were recorded.
LIVE_SIZES = [
    "Standard_ND96isr_H100_v5",
    "Standard_NC40ads_H100_v5",
    "Standard_NC24ads_A100_v4",
    "Standard_NV36ads_A10_v5",
    "Standard_NC4as_T4_v3",
]
LIVE_REGIONS = [
    "eastus",
    "eastus2",
    "southcentralus",
    "westus2",
    "westus3",
    "centralus",
    "northcentralus",
    "westus",
    "westcentralus",
]


class FakeError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.error = SimpleNamespace(code=code)


class FakeGraph:
    """Answers each query from a list of pages per query kind."""

    def __init__(self, evictions=None, prices=None, fail=None) -> None:
        self.pages = {"eviction": evictions or [[]], "price": prices or [[]]}
        self.fail = fail or {}
        self.requests: list[dict] = []

    def resources(self, request: dict):
        self.requests.append(request)
        kind = "eviction" if "skuspotevictionrate" in request["query"] else "price"
        if kind in self.fail:
            raise self.fail[kind]
        index = sum(1 for r in self.requests if r["query"] == request["query"]) - 1
        pages = self.pages[kind]
        token = f"page-{index + 1}" if index + 1 < len(pages) else None
        return SimpleNamespace(data=pages[index], skip_token=token)


def request(query, skip_token):
    return {"query": query, "skip_token": skip_token}


@pytest.fixture
def make_ctx(monkeypatch):
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", SUBSCRIPTION)

    def make(graph, sizes=SIZES, regions=REGIONS, **extra):
        return ProbeContext(
            config={"regions": regions, "spot_sizes": sizes, **extra},
            provider="azure",
            run_id="run-1",
            experiment="ftg",
            store=None,
            clients=Clients(azure_factory={"resourcegraph": graph}.__getitem__),
            now_fn=lambda: datetime(2026, 10, 11, 12, 0, tzinfo=UTC),
        )

    return make


def eviction(sku, location, rate):
    return {"skuName": sku.lower(), "location": location, "spotEvictionRate": rate}


def price(sku, location, usd, effective="2026-10-01T00:00:00Z"):
    return {
        "skuName": sku.lower(),
        "osType": "Linux",
        "location": location,
        "latestSpotPriceUSD": usd,
        "latestSpotPriceEffectiveDate": effective,
    }


def by_pair(rows):
    return {(r.sku, r.region): r for r in rows}


def test_loader_finds_the_probe_with_its_cadence():
    probes = {p.probe_type: p for p in load_probes(packages_for("azure"))}
    probe = probes["az_spot"]
    assert isinstance(probe, SpotResourceGraphProbe)
    assert (probe.provider, probe.default_cadence) == ("azure", "5m")


def test_queries_filter_to_the_config_sizes_regions_and_linux():
    assert eviction_query(SIZES, REGIONS) == (
        "SpotResources\n"
        "| where type =~ 'microsoft.compute/skuspotevictionrate/location'\n"
        "| where sku.name in~ ('Standard_A', 'Standard_B')\n"
        "| where location in~ ('eastus', 'westus')\n"
        "| project skuName = tostring(sku.name), location, "
        "spotEvictionRate = tostring(properties.evictionRate)"
    )
    assert price_query(SIZES, REGIONS) == (
        "SpotResources\n"
        "| where type =~ 'microsoft.compute/skuspotpricehistory/ostype/location'\n"
        "| where sku.name in~ ('Standard_A', 'Standard_B')\n"
        "| where properties.osType =~ 'linux'\n"
        "| where location in~ ('eastus', 'westus')\n"
        "| project skuName = tostring(sku.name), osType = tostring(properties.osType), "
        "location, latestSpotPriceUSD = todouble(properties.spotPrices[0].priceUSD), "
        "latestSpotPriceEffectiveDate = tostring(properties.spotPrices[0].effectiveDate)"
    )


@pytest.mark.parametrize("bad", ["eastus'", "a b", "x|y", ""])
def test_unsafe_names_are_refused(bad):
    with pytest.raises(ValueError):
        kql_list([bad])


def test_rows_map_eviction_and_price(make_ctx):
    graph = FakeGraph(
        evictions=[
            [eviction("Standard_A", "eastus", "5-10"), eviction("Standard_B", "westus", "20+")]
        ],
        prices=[[price("Standard_A", "eastus", 1.25), price("Standard_A", "westus", 0.5)]],
    )
    probe = SpotResourceGraphProbe(make_request=request)
    rows = probe.run(make_ctx(graph))

    assert len(rows) == len(SIZES) * len(REGIONS)
    assert [(r.region, r.sku) for r in rows] == [(r, s) for r in REGIONS for s in SIZES]
    pairs = by_pair(rows)
    a_east = pairs[("Standard_A", "eastus")]
    assert a_east.details == {
        "eviction_rate": "5-10",
        "eviction_rate_min_pct": 5,
        "eviction_rate_max_pct": 10,
        "price": 1.25,
        "price_effective_date": "2026-10-01T00:00:00Z",
        "currency": "USD",
        "os_type": "Linux",
        "eviction_error_code": None,
        "price_error_code": None,
    }
    assert (a_east.outcome, a_east.error_code) == ("success", None)
    a_west = pairs[("Standard_A", "westus")]
    assert (a_west.details["eviction_rate"], a_west.details["price"]) == (None, 0.5)
    assert a_west.outcome == "success"
    b_west = pairs[("Standard_B", "westus")]
    assert (b_west.details["eviction_rate"], b_west.details["price"]) == ("20+", None)
    assert (b_west.details["eviction_rate_min_pct"], b_west.details["eviction_rate_max_pct"]) == (
        20,
        None,
    )
    assert b_west.details["price_effective_date"] is None
    assert b_west.details["os_type"] == "linux"
    for row in rows:
        assert row.validate() == []
        assert row.probe_type == "az_spot"
        assert row.zone is None
        assert row.placement_group_mode == "n/a"
        assert set(row.details) == {
            "eviction_rate",
            "eviction_rate_min_pct",
            "eviction_rate_max_pct",
            "price",
            "price_effective_date",
            "currency",
            "os_type",
            "eviction_error_code",
            "price_error_code",
        }
    assert len(graph.requests) == 2


def test_a_missing_pair_is_none_with_null_keys(make_ctx):
    graph = FakeGraph(evictions=[[eviction("Standard_A", "eastus", "0-5")]])
    rows = SpotResourceGraphProbe(make_request=request).run(make_ctx(graph))
    missing = by_pair(rows)[("Standard_B", "westus")]
    assert missing.outcome == "none"
    assert missing.error_code is None
    assert missing.details["eviction_rate"] is None
    assert missing.details["price"] is None
    assert missing.details["eviction_rate_min_pct"] is None
    assert missing.details["eviction_rate_max_pct"] is None
    assert missing.details["price_effective_date"] is None
    assert missing.validate() == []


@pytest.mark.parametrize(
    ("rate", "bounds"),
    [
        ("0-5", (0, 5)),
        ("5-10", (5, 10)),
        ("15-20", (15, 20)),
        ("20+", (20, None)),
        (" 2.5 - 7.5 ", (2.5, 7.5)),
        ("high", (None, None)),
        ("5-", (None, None)),
        (None, (None, None)),
    ],
)
def test_eviction_bounds_in_percent(rate, bounds):
    assert eviction_bounds(rate) == bounds


def test_an_unknown_eviction_rate_keeps_the_raw_string(make_ctx):
    graph = FakeGraph(evictions=[[eviction("Standard_A", "eastus", "very low")]])
    rows = SpotResourceGraphProbe(make_request=request).run(make_ctx(graph))
    row = by_pair(rows)[("Standard_A", "eastus")]
    assert row.outcome == "success"
    assert row.details["eviction_rate"] == "very low"
    assert (row.details["eviction_rate_min_pct"], row.details["eviction_rate_max_pct"]) == (
        None,
        None,
    )


def test_all_pages_are_read(make_ctx):
    graph = FakeGraph(
        evictions=[
            [eviction("Standard_A", "eastus", "0-5")],
            [eviction("Standard_B", "westus", "10-15")],
        ]
    )
    rows = SpotResourceGraphProbe(make_request=request).run(make_ctx(graph))
    assert by_pair(rows)[("Standard_B", "westus")].details["eviction_rate"] == "10-15"
    tokens = [r["skip_token"] for r in graph.requests if "evictionrate" in r["query"]]
    assert tokens == [None, "page-1"]


def test_a_failed_query_writes_error_rows(make_ctx):
    graph = FakeGraph(
        evictions=[[eviction("Standard_A", "eastus", "0-5")]],
        fail={"price": FakeError("RateLimiting")},
    )
    rows = SpotResourceGraphProbe(make_request=request).run(make_ctx(graph))
    assert {(r.outcome, r.error_code) for r in rows} == {("error", "RateLimiting")}
    assert by_pair(rows)[("Standard_A", "eastus")].details["eviction_rate"] == "0-5"
    for row in rows:
        assert row.details["eviction_error_code"] is None
        assert row.details["price_error_code"] == "RateLimiting"


def test_both_failed_queries_keep_their_codes_in_details(make_ctx):
    graph = FakeGraph(fail={"eviction": FakeError("Throttled"), "price": FakeError("BadGateway")})
    rows = SpotResourceGraphProbe(make_request=request).run(make_ctx(graph))
    assert {(r.outcome, r.error_code) for r in rows} == {("error", "Throttled")}
    for row in rows:
        assert row.details["eviction_error_code"] == "Throttled"
        assert row.details["price_error_code"] == "BadGateway"


def test_the_second_query_code_is_kept_when_only_it_fails_first_is_null(make_ctx):
    graph = FakeGraph(fail={"eviction": FakeError("Throttled")})
    rows = SpotResourceGraphProbe(make_request=request).run(make_ctx(graph))
    assert {r.error_code for r in rows} == {"Throttled"}
    assert {r.details["price_error_code"] for r in rows} == {None}


def test_the_sdk_request_has_the_query_and_the_page_options():
    import_or_skip("azure.mgmt.resourcegraph")
    from ftg_prober.azure.spot_resource_graph import sdk_query_request

    built = sdk_query_request("SpotResources", "token")
    assert built.query == "SpotResources"
    assert (built.options.top, built.options.skip_token) == (1000, "token")
    assert built.options.result_format == "objectArray"


def test_the_sdk_request_is_tenant_scope_also_with_a_subscription():
    """A subscription-scope query of SpotResources returns 0 rows (live check of 2026-10-12)."""
    import_or_skip("azure.mgmt.resourcegraph")
    from ftg_prober.azure.spot_resource_graph import sdk_query_request

    built = sdk_query_request("SpotResources", None)
    assert built.subscriptions is None
    assert built.management_groups is None
    assert "subscriptions" not in built.serialize()


def recorded(name):
    """A recorded Resource Graph REST response, as the SDK response object."""
    body = json.loads((FIXTURES / name).read_text())
    return SimpleNamespace(data=body["data"], skip_token=body.get("$skipToken"))


def test_recorded_live_responses_give_values_and_separate_none_rows(make_ctx):
    """Responses of a tenant-scope run on 2026-10-12 for the 5 config sizes in 9 regions."""
    eviction_page = recorded("az_spot_eviction_response.json")
    price_page = recorded("az_spot_price_response.json")
    graph = FakeGraph(evictions=[eviction_page.data], prices=[price_page.data])
    rows = SpotResourceGraphProbe(make_request=request).run(
        make_ctx(graph, sizes=LIVE_SIZES, regions=LIVE_REGIONS)
    )

    assert len(rows) == 45
    assert all(row.validate() == [] for row in rows)
    pairs = by_pair(rows)
    none_pairs = {(r.sku, r.region) for r in rows if r.outcome == "none"}
    missing = {
        ("Standard_NC40ads_H100_v5", "westcentralus"),
        ("Standard_NV36ads_A10_v5", "westcentralus"),
        ("Standard_NC4as_T4_v3", "westcentralus"),
    }
    zero_price = ("Standard_NC24ads_A100_v4", "westcentralus")
    assert none_pairs == missing | {zero_price}
    for pair in missing:
        assert pairs[pair].details["price"] is None
        assert pairs[pair].details["eviction_rate"] is None
    # The live row with priceUSD 0.0 and effectiveDate 2025-01-01 keeps its raw values
    # (spec 0009, decision A15).
    assert pairs[zero_price].details["price"] == 0.0
    assert pairs[zero_price].details["price_effective_date"] == "2025-01-01T00:00:00Z"
    assert pairs[zero_price].details["eviction_rate"] is None
    success = [r for r in rows if r.outcome == "success"]
    assert len(success) == 41
    assert all(r.details["price"] for r in success)
    assert sum(r.details["eviction_rate"] is not None for r in success) == 33
    assert {r.error_code for r in rows} == {None}

    h100 = pairs[("Standard_ND96isr_H100_v5", "eastus")]
    assert h100.details == {
        "eviction_rate": "0-5",
        "eviction_rate_min_pct": 0,
        "eviction_rate_max_pct": 5,
        "price": 18.169536,
        "price_effective_date": "2026-01-01T00:00:00Z",
        "currency": "USD",
        "os_type": "linux",
        "eviction_error_code": None,
        "price_error_code": None,
    }
    open_range = pairs[("Standard_NC40ads_H100_v5", "westus2")]
    assert open_range.details["eviction_rate"] == "20+"
    assert open_range.details["eviction_rate_min_pct"] == 20
    assert open_range.details["eviction_rate_max_pct"] is None
    price_only = pairs[("Standard_ND96isr_H100_v5", "westus")]
    assert (price_only.outcome, price_only.details["eviction_rate"]) == ("success", None)
    assert price_only.details["eviction_rate_min_pct"] is None
    assert price_only.details["price"] == 23.620397
    assert price_only.details["price_effective_date"] == "2025-10-01T00:00:00Z"
    rates = {r.details["eviction_rate"] for r in success} - {None}
    assert rates == {"0-5", "5-10", "10-15", "15-20", "20+"}
    for row in success:
        if row.details["eviction_rate"] is not None:
            assert row.details["eviction_rate_min_pct"] is not None


NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("usd", "effective", "valid"),
    [
        (1.25, "2026-10-01T00:00:00Z", True),
        (0.0, "2026-10-01T00:00:00Z", False),
        (0, "2026-10-01T00:00:00Z", False),
        (-0.5, "2026-10-01T00:00:00Z", False),
        (1.25, "2025-04-19T12:00:00Z", True),  # exactly 540 days old
        (1.25, "2025-04-19T11:59:59Z", False),  # 1 second more
        (1.25, "2025-01-01T00:00:00Z", False),
        (1.25, "2025-09-01T00:00:00Z", True),
        (1.25, "2027-01-01T00:00:00Z", True),  # a future date is not old
        (1.25, "2025-01-01T00:00:00", False),  # no time zone: UTC
        (1.25, "not a date", True),  # an unknown age is not old
        (1.25, None, True),
        (None, "2026-10-01T00:00:00Z", True),  # no number: the old rule applies
    ],
)
def test_price_is_signal(usd, effective, valid):
    assert price_is_signal(usd, effective, NOW, DEFAULT_PRICE_MAX_AGE_DAYS) is valid


def test_a_zero_or_old_price_is_a_none_row_with_raw_values(make_ctx):
    graph = FakeGraph(
        evictions=[[eviction("Standard_A", "eastus", "0-5")]],
        prices=[
            [
                price("Standard_A", "eastus", 0.0),
                price("Standard_B", "eastus", 1.25, effective="2025-01-01T00:00:00Z"),
                price("Standard_A", "westus", 1.25, effective="2026-09-01T00:00:00Z"),
            ]
        ],
    )
    rows = SpotResourceGraphProbe(make_request=request).run(make_ctx(graph))
    pairs = by_pair(rows)
    zero = pairs[("Standard_A", "eastus")]
    assert (zero.outcome, zero.error_code) == ("none", None)
    assert zero.details["price"] == 0.0
    assert zero.details["price_effective_date"] == "2026-10-01T00:00:00Z"
    assert zero.details["eviction_rate"] == "0-5"
    old = pairs[("Standard_B", "eastus")]
    assert old.outcome == "none"
    assert (old.details["price"], old.details["price_effective_date"]) == (
        1.25,
        "2025-01-01T00:00:00Z",
    )
    assert pairs[("Standard_A", "westus")].outcome == "success"
    assert all(row.validate() == [] for row in rows)


def test_a_failed_query_wins_over_an_invalid_price(make_ctx):
    graph = FakeGraph(
        prices=[[price("Standard_A", "eastus", 0.0)]],
        fail={"eviction": FakeError("RateLimiting")},
    )
    rows = SpotResourceGraphProbe(make_request=request).run(make_ctx(graph))
    assert by_pair(rows)[("Standard_A", "eastus")].outcome == "error"


def test_the_price_age_limit_is_a_config_value(make_ctx):
    graph = FakeGraph(prices=[[price("Standard_A", "eastus", 1.25, "2026-09-01T00:00:00Z")]])
    probe = SpotResourceGraphProbe(make_request=request)
    rows = probe.run(make_ctx(graph, spot_price_max_age_days=30))
    assert by_pair(rows)[("Standard_A", "eastus")].outcome == "none"
    assert price_max_age_days({}) == 540


@pytest.mark.parametrize("bad", [0, -1, 1.5, "540", True, None])
def test_a_bad_price_age_limit_stops_the_call(make_ctx, bad):
    probe = SpotResourceGraphProbe(make_request=request)
    with pytest.raises(ValueError, match="spot_price_max_age_days"):
        probe.run(make_ctx(FakeGraph(), spot_price_max_age_days=bad))


def test_a_provider_error_gives_rows_with_no_message_and_1_log_line(make_ctx, caplog):
    message = "Throttled. /subscriptions/not-a-real-id isn't permitted"

    class SdkError(Exception):
        def __init__(self) -> None:
            super().__init__(f"(RateLimiting) {message}")
            self.error = SimpleNamespace(code="RateLimiting", message=message)
            self.status_code = 429

    graph = FakeGraph(fail={"price": SdkError()})
    with caplog.at_level("WARNING"):
        rows = SpotResourceGraphProbe(make_request=request).run(make_ctx(graph))
    assert {r.error_code for r in rows} == {"RateLimiting"}
    assert all(message not in repr(r) and "message" not in str(r.details).lower() for r in rows)
    (line,) = [r.getMessage() for r in caplog.records if "code=" in r.getMessage()]
    assert "probe_type=az_spot" in line
    assert "code=RateLimiting" in line
    assert "status=429" in line
    assert repr(message) in line


def test_sim_rows_have_the_value_types_of_a_real_row(make_ctx, repo_config, monkeypatch):
    """a sim row has the keys and value types of a real row of its kind: a
    closed bucket, the open `20+` bucket, an unknown bucket (null), a `none` row (no data), and an
    `error` row (the eviction query failed, the price is known)."""
    import random

    from ftg_prober import sim
    from ftg_prober.sim import sim_calls
    from tests.shapes import value_types, variant
    from tests.test_sim import END

    sizes = [*SIZES, "Standard_C", "Standard_D", "Standard_E"]
    graph = FakeGraph(
        evictions=[[eviction(sizes[0], "eastus", "5-10"), eviction(sizes[1], "eastus", "20+")]],
        prices=[
            [
                price(sizes[0], "eastus", 1.25),
                price(sizes[1], "eastus", 1.5),
                price(sizes[2], "eastus", 2.0),
                # older than spot_price_max_age_days (540): a `none` row with the price
                price(sizes[4], "eastus", 2.5, "2020-01-01T00:00:00Z"),
            ]
        ],
    )
    rows = SpotResourceGraphProbe(make_request=request).run(make_ctx(graph, sizes=sizes))
    pairs = by_pair(rows)
    real = {
        variant(pairs[(sizes[name], "eastus")]): pairs[(sizes[name], "eastus")]
        for name in (0, 1, 2, 3, 4)
    }
    failing = FakeGraph(
        evictions=[[]],
        prices=[[price(sizes[0], "eastus", 1.25)]],
        fail={"eviction": FakeError("X")},
    )
    error = SpotResourceGraphProbe(make_request=request).run(make_ctx(failing, sizes=sizes))
    error = by_pair(error)[(sizes[0], "eastus")]
    assert error.outcome == "error"
    real[variant(error)] = error
    # A region with no price: the failed query gives an `error` row with a null price.
    no_price = FakeGraph(fail={"eviction": FakeError("X")})
    error = by_pair(SpotResourceGraphProbe(make_request=request).run(make_ctx(no_price)))
    error = error[(SIZES[0], "eastus")]
    real[variant(error)] = error
    assert len(real) == 7
    config = repo_config.azure
    assert "westcentralus" in config["regions"]
    monkeypatch.setattr(sim, "AZ_SPOT_ERROR_SHARE", 0.5)
    seen = set()
    for seed in range(20):
        for call in sim_calls("az_spot", config, END, random.Random(seed)):
            for row in call.rows:
                seen.add(variant(row))
                assert value_types(row) == value_types(real[variant(row)]), variant(row)
    assert seen == set(real)
