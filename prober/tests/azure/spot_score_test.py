"""Tests of the Azure Spot Placement Score probe. Fakes only: no network, no credential."""

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from ftg_prober.azure.spot_score import (
    ARM_SCOPE,
    OUTCOME_BY_SCORE,
    RATE_LIMITED,
    RETRY_WAIT_S,
    UNKNOWN_ZONE,
    SpotScoreProbe,
    score_outcome,
)
from ftg_prober.probes import Clients, ProbeContext, load_probes, packages_for

SUBSCRIPTION = "test-subscription"
REGIONS = [
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
SIZES = [f"Standard_Size{i}" for i in range(7)]


class FakeCredential:
    def __init__(self) -> None:
        self.scopes: list[str] = []

    def get_token(self, *scopes: str):
        self.scopes.extend(scopes)
        return SimpleNamespace(token="fake-token", expires_on=0)


class FakeResponse:
    def __init__(self, status_code: int, body, headers=None, text="") -> None:
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}
        self.text = text

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def scores_for(body: dict, score="High") -> dict:
    """A 200 body with 1 item per size and region, or per size, region, and zone."""
    items = []
    for region in body["desiredLocations"]:
        for size in body["desiredSizes"]:
            zones = ["1", "2", "3"] if body["availabilityZones"] else [None]
            for zone in zones:
                value = score(size["sku"], region) if callable(score) else score
                item = {"sku": size["sku"], "region": region, "isQuotaAvailable": True}
                item["score"] = value
                if zone:
                    item["availabilityZone"] = zone
                items.append(item)
    return {**body, "placementScores": items}


class FakePost:
    """Records each call. `handler(call_index, body)` returns a FakeResponse."""

    def __init__(self, handler=None) -> None:
        self.calls: list[tuple[str, dict, dict]] = []
        self.handler = handler or (lambda i, body: FakeResponse(200, scores_for(body)))

    def __call__(self, url, headers, body):
        self.calls.append((url, headers, body))
        return self.handler(len(self.calls) - 1, body)


@pytest.fixture
def credential():
    return FakeCredential()


@pytest.fixture
def make_ctx(credential, monkeypatch):
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", SUBSCRIPTION)

    def make(sizes=None, regions=None):
        config = {"regions": regions or REGIONS, "spot_sizes": sizes or SIZES}
        return ProbeContext(
            config=config,
            provider="azure",
            run_id="run-1",
            experiment="ftg",
            store=None,
            clients=Clients(azure_factory={"credential": credential}.__getitem__),
            now_fn=lambda: datetime(2026, 10, 11, 12, 0, tzinfo=UTC),
        )

    return make


def regional(rows):
    return [r for r in rows if r.details["scope"] == "regional"]


def zonal(rows):
    return [r for r in rows if r.details["scope"] == "zonal"]


def test_loader_finds_the_probe_with_its_cadence():
    probes = {p.probe_type: p for p in load_probes(packages_for("azure"))}
    probe = probes["az_spot_score"]
    assert isinstance(probe, SpotScoreProbe)
    assert (probe.provider, probe.default_cadence) == ("azure", "15m")


def test_requests_group_5_sizes_and_8_regions(make_ctx, credential):
    post = FakePost()
    rows = SpotScoreProbe(post=post, sleep=pytest.fail).run(make_ctx())

    regional_calls = [c for c in post.calls if not c[2]["availabilityZones"]]
    zonal_calls = [c for c in post.calls if c[2]["availabilityZones"]]
    # 7 sizes in groups of 5, 9 regions in groups of 8: 2 x 2 regional requests.
    assert len(regional_calls) == 4
    # 1 zonal request per region and size group: 9 x 2.
    assert len(zonal_calls) == 18
    pairs = []
    for url, headers, body in regional_calls:
        assert 1 <= len(body["desiredSizes"]) <= 5
        assert 1 <= len(body["desiredLocations"]) <= 8
        assert body["desiredCount"] == 1
        assert body["availabilityZones"] is False
        assert headers["Authorization"] == "Bearer fake-token"
        assert url == (
            f"https://management.azure.com/subscriptions/{SUBSCRIPTION}/providers/"
            f"Microsoft.Compute/locations/{body['desiredLocations'][0]}/placementScores/"
            "spot/generate?api-version=2025-06-05"
        )
        pairs += [(s["sku"], r) for r in body["desiredLocations"] for s in body["desiredSizes"]]
    assert sorted(pairs) == sorted((s, r) for s in SIZES for r in REGIONS)
    for _, _, body in zonal_calls:
        assert len(body["desiredLocations"]) == 1
        assert len(body["desiredSizes"]) <= 5
    assert set(credential.scopes) == {ARM_SCOPE}

    assert len(regional(rows)) == len(SIZES) * len(REGIONS)
    assert len(zonal(rows)) == len(SIZES) * len(REGIONS) * 3


def test_regional_rows_follow_the_record_contract(make_ctx):
    rows = SpotScoreProbe(post=FakePost(), sleep=pytest.fail).run(make_ctx())
    for row in regional(rows):
        assert row.validate() == []
        assert row.probe_type == "az_spot_score"
        assert row.provider == "azure"
        assert row.zone is None
        assert row.placement_group_mode == "n/a"
        assert (row.n_requested, row.n_placed) == (1, None)
        assert row.run_id == "run-1"
        assert row.simulated is False
        assert row.details == {
            "score": "High",
            "desired_count": 1,
            "is_quota_available": True,
            "scope": "regional",
        }
        assert row.outcome == "success"


def test_zonal_rows_set_the_zone(make_ctx):
    rows = SpotScoreProbe(post=FakePost(), sleep=pytest.fail).run(
        make_ctx(sizes=["Standard_A"], regions=["eastus"])
    )
    zones = sorted(r.zone for r in zonal(rows))
    assert zones == ["1", "2", "3"]
    assert [r.zone for r in regional(rows)] == [None]
    assert all(r.validate() == [] for r in rows)


@pytest.mark.parametrize(
    ("score", "outcome", "error_code"),
    [
        ("High", "success", None),
        ("Medium", "success", None),
        ("Low", "success", None),
        ("RestrictedSkuNotAvailable", "restricted", "RestrictedSkuNotAvailable"),
        ("InsufficientQuota", "quota", "InsufficientQuota"),
        ("DataNotFoundOrStale", "none", None),
        ("SomethingNew", "error", "SomethingNew"),
    ],
)
def test_each_score_value_maps_to_its_outcome(make_ctx, score, outcome, error_code):
    post = FakePost(lambda i, body: FakeResponse(200, scores_for(body, score)))
    rows = SpotScoreProbe(post=post, sleep=pytest.fail).run(
        make_ctx(sizes=["Standard_A"], regions=["eastus"])
    )
    row = regional(rows)[0]
    assert (row.outcome, row.error_code, row.details["score"]) == (outcome, error_code, score)


def test_outcome_map_covers_the_documented_values():
    assert set(OUTCOME_BY_SCORE) == {
        "High",
        "Medium",
        "Low",
        "RestrictedSkuNotAvailable",
        "InsufficientQuota",
        "DataNotFoundOrStale",
    }
    assert score_outcome(None) == "error"
    assert score_outcome(7) == "error"


def test_a_missing_pair_is_none(make_ctx):
    def handler(i, body):
        full = scores_for(body)
        full["placementScores"] = [
            item for item in full["placementScores"] if item["sku"] != "Standard_B"
        ]
        return FakeResponse(200, full)

    rows = SpotScoreProbe(post=FakePost(handler), sleep=pytest.fail).run(
        make_ctx(sizes=["Standard_A", "Standard_B"], regions=["eastus"])
    )
    by_sku = {r.sku: r for r in regional(rows)}
    assert by_sku["Standard_A"].outcome == "success"
    assert by_sku["Standard_B"].outcome == "none"
    assert by_sku["Standard_B"].details["score"] is None
    assert by_sku["Standard_B"].validate() == []
    # A zonal request has no known zone for a missing pair, thus no zonal row.
    assert {r.sku for r in zonal(rows)} == {"Standard_A"}


def test_429_then_success_waits_60_s_and_retries_once(make_ctx):
    sleeps: list[float] = []

    def handler(i, body):
        if i == 0:
            return FakeResponse(429, {"error": {"code": "TooManyRequests"}})
        return FakeResponse(200, scores_for(body))

    post = FakePost(handler)
    rows = SpotScoreProbe(post=post, sleep=sleeps.append).run(
        make_ctx(sizes=["Standard_A"], regions=["eastus"])
    )
    assert sleeps == [RETRY_WAIT_S] == [60]
    # The regional request twice, then 1 zonal request.
    assert len(post.calls) == 3
    assert post.calls[0][2] == post.calls[1][2]
    assert regional(rows)[0].outcome == "success"
    assert regional(rows)[0].error_code is None


def test_429_twice_writes_error_rows(make_ctx):
    sleeps: list[float] = []

    def handler(i, body):
        if not body["availabilityZones"]:
            return FakeResponse(429, {"error": {"code": "TooManyRequests"}})
        return FakeResponse(200, scores_for(body))

    post = FakePost(handler)
    rows = SpotScoreProbe(post=post, sleep=sleeps.append).run(
        make_ctx(sizes=["Standard_A", "Standard_B"], regions=["eastus", "westus"])
    )
    assert sleeps == [60]
    regional_calls = [c for c in post.calls if not c[2]["availabilityZones"]]
    assert len(regional_calls) == 2
    reg = regional(rows)
    assert len(reg) == 4
    for row in reg:
        assert row.outcome == "error"
        assert row.error_code == "TooManyRequests"
        assert row.zone is None
        assert row.details == {
            "score": None,
            "desired_count": 1,
            "scope": "regional",
            "http_status": 429,
        }
        assert row.validate() == []
    assert all(r.outcome == "success" for r in zonal(rows))


def test_429_twice_without_a_code_uses_the_status(make_ctx):
    post = FakePost(lambda i, body: FakeResponse(429, ValueError("no JSON")))
    rows = SpotScoreProbe(post=post, sleep=lambda s: None).run(
        make_ctx(sizes=["Standard_A"], regions=["eastus"])
    )
    assert {r.error_code for r in rows} == {"HTTP 429"}
    assert {r.zone for r in zonal(rows)} == {UNKNOWN_ZONE}
    # 2 requests, each tried twice.
    assert len(post.calls) == 4


# Recorded: the plain-text 429 of the public report in an Azure PowerShell issue
# (https://github.com/Azure/azure-powershell/issues/28765). It has no `Retry-After`.
PLAIN_429_TEXT = (
    "You have reached the maximum number of requests allowed. Please try again after 3600 seconds."
)
# Recorded: the sample Compute 429 of the Azure throttling troubleshooting page
# (https://learn.microsoft.com/troubleshoot/azure/virtual-machines/windows/troubleshooting-throttling-errors).
# It has `Retry-After: 1200`, a top-level `code`, and no "try again after" text. The page does
# not name Spot Placement Score.
COMPUTE_429_BODY = {
    "code": "OperationNotAllowed",
    "message": "The server rejected the request because too many requests have been received "
    "for this subscription.",
    "details": [{"code": "TooManyRequests", "target": "HighCostGet"}],
}
# Synthetic, not observed: a JSON body that holds the "try again after N seconds" text.
SYNTHETIC_JSON_429_BODY = {
    "error": {
        "code": "TooManyRequests",
        "message": "Too many requests. Please try again after 3600 seconds.",
    }
}


def plain_429():
    return FakeResponse(429, ValueError("Expected '{'"), text=PLAIN_429_TEXT)


def header_429():
    return FakeResponse(429, COMPUTE_429_BODY, headers={"Retry-After": "1200"}, text="{}")


def body_429():
    return FakeResponse(429, SYNTHETIC_JSON_429_BODY, text=str(SYNTHETIC_JSON_429_BODY))


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.mark.parametrize(
    ("make_response", "code", "wait"),
    [
        (plain_429, "HTTP 429", 3600),
        (header_429, "HTTP 429", 1200),
        (body_429, "TooManyRequests", 3600),
    ],
)
def test_a_long_429_skips_the_retry_and_the_rest_of_the_run(make_ctx, make_response, code, wait):
    sleeps: list[float] = []
    post = FakePost(lambda i, body: make_response())
    rows = SpotScoreProbe(post=post, sleep=sleeps.append).run(
        make_ctx(sizes=["Standard_A"], regions=["eastus", "westus"])
    )
    assert sleeps == []
    assert len(post.calls) == 1
    # 1 regional request (2 pairs), 2 zonal requests (1 pair each).
    assert len(rows) == 4
    first, skipped = rows[:2], rows[2:]
    for row in first:
        assert (row.outcome, row.error_code) == ("error", code)
        assert row.details["http_status"] == 429
        assert row.details["retry_after_s"] == wait
    for row in skipped:
        assert (row.outcome, row.error_code) == ("error", RATE_LIMITED)
        assert row.zone == UNKNOWN_ZONE
        assert row.details["retry_after_s"] == wait
        assert "http_status" not in row.details
    assert all(r.validate() == [] for r in rows)


def test_the_header_wins_over_the_body(make_ctx):
    response = FakeResponse(
        429, SYNTHETIC_JSON_429_BODY, headers={"Retry-After": "30"}, text=PLAIN_429_TEXT
    )
    sleeps: list[float] = []
    post = FakePost(lambda i, body: response)
    SpotScoreProbe(post=post, sleep=sleeps.append).run(
        make_ctx(sizes=["Standard_A"], regions=["eastus"])
    )
    assert sleeps == [60, 60]
    assert len(post.calls) == 4


def test_a_429_with_a_wait_at_the_budget_still_retries(make_ctx):
    sleeps: list[float] = []
    text = "Please try again after 60 seconds."
    post = FakePost(lambda i, body: FakeResponse(429, ValueError("x"), text=text))
    SpotScoreProbe(post=post, sleep=sleeps.append).run(
        make_ctx(sizes=["Standard_A"], regions=["eastus"])
    )
    assert sleeps == [60, 60]


def test_a_429_with_an_unreadable_wait_still_retries(make_ctx):
    sleeps: list[float] = []
    response = FakeResponse(
        429, ValueError("x"), headers={"Retry-After": "soon"}, text="Slow down."
    )
    post = FakePost(lambda i, body: response)
    SpotScoreProbe(post=post, sleep=sleeps.append).run(
        make_ctx(sizes=["Standard_A"], regions=["eastus"])
    )
    assert sleeps == [60, 60]


def test_the_next_runs_send_nothing_until_the_wait_ends(make_ctx):
    clock = FakeClock()
    long_wait = {"on": True}

    def handler(i, body):
        if long_wait["on"]:
            long_wait["on"] = False
            return plain_429()
        return FakeResponse(200, scores_for(body))

    post = FakePost(handler)
    probe = SpotScoreProbe(post=post, sleep=lambda s: None, clock=clock)
    ctx = make_ctx(sizes=["Standard_A"], regions=["eastus"])
    probe.run(ctx)
    assert len(post.calls) == 1
    clock.now += 900
    rows = probe.run(ctx)
    assert len(post.calls) == 1
    assert {(r.outcome, r.error_code) for r in rows} == {("error", RATE_LIMITED)}
    assert {r.details["retry_after_s"] for r in rows} == {2700}
    clock.now += 2700
    rows = probe.run(ctx)
    assert len(post.calls) == 3
    assert {r.outcome for r in rows} == {"success"}


def test_other_errors_do_not_retry(make_ctx):
    post = FakePost(lambda i, body: FakeResponse(400, {"error": {"code": "BadRequest"}}))
    rows = SpotScoreProbe(post=post, sleep=pytest.fail).run(
        make_ctx(sizes=["Standard_A"], regions=["eastus"])
    )
    assert len(post.calls) == 2
    assert {(r.outcome, r.error_code) for r in rows} == {("error", "BadRequest")}


def test_a_failed_call_becomes_error_rows(make_ctx):
    def handler(i, body):
        raise ConnectionError("no route")

    rows = SpotScoreProbe(post=FakePost(handler), sleep=pytest.fail).run(
        make_ctx(sizes=["Standard_A"], regions=["eastus"])
    )
    assert {(r.outcome, r.error_code) for r in rows} == {("error", "ConnectionError")}
    assert "http_status" not in regional(rows)[0].details


def test_a_200_without_a_json_object_is_an_error(make_ctx):
    post = FakePost(lambda i, body: FakeResponse(200, ["not", "an", "object"]))
    rows = SpotScoreProbe(post=post, sleep=pytest.fail).run(
        make_ctx(sizes=["Standard_A"], regions=["eastus"])
    )
    assert {(r.outcome, r.error_code) for r in rows} == {("error", "InvalidResponse")}


def test_a_200_without_a_json_object_logs_the_body_text(make_ctx, caplog):
    body = "<html>gateway said: not json</html>"
    post = FakePost(lambda i, b: FakeResponse(200, ValueError("bad json"), text=body))
    with caplog.at_level("WARNING"):
        rows = SpotScoreProbe(post=post, sleep=pytest.fail).run(
            make_ctx(sizes=["Standard_A"], regions=["eastus"])
        )
    assert {r.error_code for r in rows} == {"InvalidResponse"}
    lines = [r.getMessage() for r in caplog.records if "code=InvalidResponse" in r.getMessage()]
    assert len(lines) == 2  # 1 for the regional request, 1 for the zonal request
    for line in lines:
        assert "gateway said: not json" in line
        assert "status=200" in line


# The exact published example response of the Azure REST API reference (Compute, API version
# 2025-06-05): https://learn.microsoft.com/en-us/rest/api/recommenderrp/spot-placement-scores/post?view=rest-recommenderrp-2025-06-05
PROVIDER_RESPONSE = (
    Path(__file__).parents[1]
    / "fixtures"
    / "provider"
    / "azure"
    / "generate_spot_placement_scores_response.json"
)


def test_the_provider_response_gives_the_real_scores(make_ctx):
    """Runs the real probe against the published example response body, unchanged. The example
    is zonal-shaped (`availabilityZones: true`, every item carries an `availabilityZone`), the
    true shape of only the probe's zonal requests; this test checks those and leaves the
    regional request's rows unchecked, rather than feed the same zonal-shaped body as a
    regional answer."""
    body = json.loads(PROVIDER_RESPONSE.read_text())
    post = FakePost(lambda i, request_body: FakeResponse(200, body))
    rows = SpotScoreProbe(post=post, sleep=pytest.fail).run(
        make_ctx(sizes=["Standard_D2_v2"], regions=["eastus", "eastus2"])
    )

    zon = zonal(rows)
    assert {(r.region, r.zone, r.outcome, r.details["score"]) for r in zon} == {
        ("eastus", "1", "success", "High"),
        ("eastus", "2", "success", "High"),
        ("eastus", "3", "success", "High"),
        ("eastus2", "1", "none", "DataNotFoundOrStale"),
        ("eastus2", "2", "success", "High"),
        ("eastus2", "3", "success", "High"),
    }
    assert all(r.details["is_quota_available"] is True for r in zon)
    assert all(r.sku == "Standard_D2_v2" for r in rows)
    assert all(r.validate() == [] for r in rows)


# A message with a space, a quote, and an ID-shaped token, as Azure returns it.
PROVIDER_MESSAGE = "Not allowed. /subscriptions/not-a-real-id isn't permitted"


def failure_lines(caplog):
    return [r.getMessage() for r in caplog.records if "code=" in r.getMessage()]


def test_a_provider_error_gives_rows_with_no_message_and_1_log_line_per_call(make_ctx, caplog):
    body = {"error": {"code": "InvalidAuthenticationToken", "message": PROVIDER_MESSAGE}}
    post = FakePost(lambda i, b: FakeResponse(401, body))
    with caplog.at_level("WARNING"):
        rows = SpotScoreProbe(post=post, sleep=lambda s: None).run(
            make_ctx(sizes=["Standard_A"], regions=["eastus"])
        )
    # A regional and a zonal request: 2 failed calls, 1 log line each.
    lines = failure_lines(caplog)
    assert len(lines) == len(post.calls) == 2
    for row in rows:
        assert "message" not in str(row.details).lower()
        assert PROVIDER_MESSAGE not in repr(row)
    for line in lines:
        assert "probe_type=az_spot_score" in line
        assert "region=eastus" in line
        assert "code=InvalidAuthenticationToken" in line
        assert "status=401" in line
        assert repr(PROVIDER_MESSAGE) in line


def test_a_failed_post_logs_the_message_and_the_rows_have_none(make_ctx, caplog):
    def boom(i, body):
        raise OSError(PROVIDER_MESSAGE)

    with caplog.at_level("WARNING"):
        rows = SpotScoreProbe(post=FakePost(boom), sleep=lambda s: None).run(
            make_ctx(sizes=["Standard_A"], regions=["eastus"])
        )
    assert {r.error_code for r in rows} == {"OSError"}
    assert all(PROVIDER_MESSAGE not in repr(r) for r in rows)
    lines = failure_lines(caplog)
    assert len(lines) == 2
    assert all(repr(PROVIDER_MESSAGE) in line for line in lines)


def test_sim_rows_have_the_value_types_of_a_real_row(make_ctx):
    """a sim row has the keys and value types of a real row of its kind: the
    outcome (success, restricted, none), the scope (regional, zonal), and a zone or no zone (a
    zonal row in a Region without zones)."""
    import random

    from ftg_prober.sim import sim_calls
    from tests.shapes import value_types, variant
    from tests.test_sim import END

    by_size = {
        "Standard_High": "High",
        "Standard_Restricted": "RestrictedSkuNotAvailable",
        "Standard_None": "DataNotFoundOrStale",
    }

    def handler(index, body):
        answer = scores_for(body, score=lambda size, region: by_size[size])
        if body["desiredLocations"] == ["westus"]:  # a Region without zones
            for item in answer["placementScores"]:
                item.pop("availabilityZone", None)
        return FakeResponse(200, answer)

    probe = SpotScoreProbe(post=FakePost(handler), sleep=pytest.fail)
    ctx = make_ctx(sizes=list(by_size), regions=["eastus", "westus"])
    real = {}
    for row in probe.run(ctx):
        real.setdefault(variant(row), row)
    assert len(real) == 9, sorted(map(str, real))  # 3 outcomes x (regional, zonal, zonal no zone)
    assert {v[6:8] for v in real} == {(True, "regional"), (False, "zonal"), (True, "zonal")}
    # The sizes of the ND family can be restricted; 3 Regions have no zones.
    config = {"regions": REGIONS, "spot_sizes": [f"Standard_ND{i}_v5" for i in range(6)]}
    seen = set()
    for seed in range(40):
        for call in sim_calls("az_spot_score", config, END, random.Random(seed)):
            for row in call.rows:
                seen.add(variant(row))
                assert variant(row) in real, variant(row)
                assert value_types(row) == value_types(real[variant(row)]), variant(row)
    assert seen == set(real), seen ^ set(real)

    # Live rows: `is_quota_available` is false on every `none` row and on about 86% of all rows.
    values = [
        (row.outcome, row.details["is_quota_available"])
        for call in sim_calls("az_spot_score", config, END, random.Random(1))
        for row in call.rows
    ]
    assert all(quota is False for outcome, quota in values if outcome == "none")
    assert 0.75 < sum(quota is False for _, quota in values) / len(values) < 0.95
