import json
from pathlib import Path

from ftg_prober.aws.spot_advisor import FEED_URL, SpotAdvisorProbe
from tests.aws.fakes import make_ctx, section
from tests.conftest import MemoryStore

RANGES = [
    {"index": 0, "label": "<5%", "dots": 0, "max": 5},
    {"index": 1, "label": "5-10%", "dots": 1, "max": 11},
    {"index": 2, "label": "10-15%", "dots": 2, "max": 16},
    {"index": 3, "label": "15-20%", "dots": 3, "max": 22},
    {"index": 4, "label": ">20%", "dots": 4, "max": 100},
]
GPU_TYPES = ["p5.48xlarge", "g5.xlarge", "g6.xlarge", "g4dn.xlarge"]
# A trimmed copy of the public feed, downloaded on 2026-10-12: all `ranges`, the Regions of
# config/targets.yaml, `Linux` only, and the GPU types of the Spot score requests.
RECORDED_FEED = Path(__file__).parent / "fixtures" / "spot_advisor_feed.json"


def feed():
    linux = {
        "p5.48xlarge": {"s": 57, "r": 0},
        "g5.xlarge": {"s": 56, "r": 4},
        "g6.xlarge": {"s": 61, "r": 2},
        "g4dn.xlarge": {"s": 60, "r": 1},
        "m5.4xlarge": {"s": 70, "r": 0},
    }
    return {
        "global_rate": "<10%",
        "instance_types": {},
        "ranges": RANGES,
        "spot_advisor": {
            "us-east-1": {"Linux": linux, "Windows": {"g5.xlarge": {"s": 1, "r": 0}}},
            "us-west-2": {"Linux": {"p5.48xlarge": {"s": 50, "r": 3}}},
        },
    }


class FakeResponse:
    def __init__(self, status_code=200, content=b""):
        self.status_code = status_code
        self.content = content


class FakeHttp:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def get(self, url, timeout):
        self.calls.append((url, timeout))
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer


def run(answer):
    store = MemoryStore()
    http = FakeHttp(answer)
    rows = SpotAdvisorProbe(http=http).run(make_ctx(section(), store=store))
    return rows, store, http


def test_fetches_the_feed_writes_the_raw_json_and_1_row_per_type_and_region():
    body = json.dumps(feed()).encode()
    rows, store, http = run(FakeResponse(200, body))

    assert http.calls == [(FEED_URL, 30)]
    assert len(store.puts) == 1
    key = store.puts[0]
    assert key.startswith(
        "experiment=ftg/provider=aws/probe_type=aws_spot_advisor/date=2026-10-11/"
    )
    assert "_raw_" in key and key.endswith(".json")
    assert store.objects[key] == body

    assert [(r.region, r.sku) for r in rows] == [
        (region, t) for region in ("us-east-1", "us-west-2") for t in GPU_TYPES
    ]
    first = rows[0]
    assert first.probe_type == "aws_spot_advisor"
    assert first.zone is None
    assert first.outcome == "success"
    assert first.details == {
        "interruption_range": "<5%",
        "interruption_range_min_pct": 0,
        "interruption_range_max_pct": 5,
        "feed_max_pct": 5,
        "savings_pct": 57,
        "range_index": 0,
    }
    assert rows[1].details["interruption_range"] == ">20%"
    assert all(r.validate() == [] for r in rows)


def test_a_type_missing_from_the_feed_gives_a_none_row():
    rows, _, _ = run(FakeResponse(200, json.dumps(feed()).encode()))
    west = [(r.sku, r.outcome) for r in rows if r.region == "us-west-2"]
    assert west == [
        ("p5.48xlarge", "success"),
        ("g5.xlarge", "none"),
        ("g6.xlarge", "none"),
        ("g4dn.xlarge", "none"),
    ]


def test_a_region_missing_from_the_feed_gives_none_rows():
    data = feed()
    del data["spot_advisor"]["us-west-2"]
    rows, _, _ = run(FakeResponse(200, json.dumps(data).encode()))
    assert {r.outcome for r in rows if r.region == "us-west-2"} == {"none"}


def errors_everywhere(rows, code):
    assert len(rows) == 2 * len(GPU_TYPES)
    assert {(r.outcome, r.error_code) for r in rows} == {("error", code)}
    assert all(r.validate() == [] for r in rows)


def test_a_failed_fetch_gives_an_error_row_for_every_type_and_region():
    class ConnectionError(Exception):
        pass

    rows, store, _ = run(ConnectionError("down"))
    errors_everywhere(rows, "ConnectionError")
    assert store.puts == []


def test_an_http_error_status_gives_error_rows():
    rows, store, _ = run(FakeResponse(503, b"busy"))
    errors_everywhere(rows, "HTTP503")
    assert store.puts == []


def test_a_body_that_does_not_parse_gives_error_rows():
    rows, store, _ = run(FakeResponse(200, b"<html>not json</html>"))
    errors_everywhere(rows, "FeedFormat")
    assert store.puts == []


def test_json_without_the_expected_keys_gives_error_rows():
    rows, _, _ = run(FakeResponse(200, json.dumps({"something": "else"}).encode()))
    errors_everywhere(rows, "FeedFormat")


def test_a_region_value_that_is_not_an_object_gives_error_rows_for_that_region():
    data = feed()
    data["spot_advisor"]["us-west-2"] = {"Linux": ["not", "an", "object"]}
    rows, _, _ = run(FakeResponse(200, json.dumps(data).encode()))
    west = {(r.outcome, r.error_code) for r in rows if r.region == "us-west-2"}
    assert west == {("error", "FeedFormat")}
    assert {r.outcome for r in rows if r.region == "us-east-1"} == {"success"}


def test_an_entry_with_an_unknown_range_index_gives_an_error_row():
    data = feed()
    data["spot_advisor"]["us-east-1"]["Linux"]["g5.xlarge"] = {"s": 56, "r": 9}
    rows, _, _ = run(FakeResponse(200, json.dumps(data).encode()))
    g5 = [r for r in rows if r.region == "us-east-1" and r.sku == "g5.xlarge"][0]
    assert (g5.outcome, g5.error_code) == ("error", "FeedFormat")


def test_an_entry_with_a_range_label_that_is_not_a_string_gives_an_error_row():
    data = feed()
    data["ranges"][4] = {"index": 4, "label": None, "max": 100}
    rows, _, _ = run(FakeResponse(200, json.dumps(data).encode()))
    g5 = [r for r in rows if r.region == "us-east-1" and r.sku == "g5.xlarge"][0]
    assert (g5.outcome, g5.error_code) == ("error", "FeedFormat")


def details_of(rows, region, sku):
    return [r.details for r in rows if r.region == region and r.sku == sku][0]


def test_the_recorded_feed_gives_the_label_bounds_and_the_feed_max_in_different_keys():
    body = RECORDED_FEED.read_bytes()
    rows, store, _ = run(FakeResponse(200, body))
    assert store.objects[store.puts[0]] == body
    assert {r.outcome for r in rows} == {"success"}
    assert all(r.validate() == [] for r in rows)

    # The labels and the feed `max` values of 2026-10-12.
    labels = {r["label"]: r["max"] for r in json.loads(body)["ranges"]}
    assert labels == {"<5%": 5, "5-10%": 11, "10-15%": 16, "15-20%": 22, ">20%": 100}

    def bounds(details):
        return (
            details["interruption_range"],
            details["interruption_range_min_pct"],
            details["interruption_range_max_pct"],
            details["feed_max_pct"],
        )

    assert bounds(details_of(rows, "us-east-1", "p5.48xlarge")) == ("<5%", 0, 5, 5)
    assert bounds(details_of(rows, "us-east-1", "g4dn.xlarge")) == ("10-15%", 10, 15, 16)
    assert bounds(details_of(rows, "us-east-1", "g5.xlarge")) == (">20%", 20, None, 100)


def test_the_label_bounds_and_the_feed_max_differ_for_each_middle_range():
    data = feed()
    linux = data["spot_advisor"]["us-east-1"]["Linux"]
    linux["g5.xlarge"] = {"s": 56, "r": 1}
    linux["g6.xlarge"] = {"s": 61, "r": 2}
    linux["g4dn.xlarge"] = {"s": 60, "r": 3}
    rows, _, _ = run(FakeResponse(200, json.dumps(data).encode()))
    got = [
        (
            details_of(rows, "us-east-1", t)["interruption_range_max_pct"],
            details_of(rows, "us-east-1", t)["feed_max_pct"],
        )
        for t in ("g5.xlarge", "g6.xlarge", "g4dn.xlarge")
    ]
    assert got == [(10, 11), (15, 16), (20, 22)]


def test_an_unknown_label_gives_a_success_row_with_the_label_as_returned_and_null_bounds():
    data = feed()
    data["ranges"].append({"index": 5, "label": "20-30%", "dots": 5, "max": 30})
    data["spot_advisor"]["us-east-1"]["Linux"]["g5.xlarge"] = {"s": 56, "r": 5}
    rows, _, _ = run(FakeResponse(200, json.dumps(data).encode()))
    g5 = [r for r in rows if r.region == "us-east-1" and r.sku == "g5.xlarge"][0]
    assert (g5.outcome, g5.error_code) == ("success", None)
    assert g5.details == {
        "interruption_range": "20-30%",
        "interruption_range_min_pct": None,
        "interruption_range_max_pct": None,
        "feed_max_pct": 30,
        "savings_pct": 56,
        "range_index": 5,
    }
    assert g5.validate() == []


def test_a_range_without_a_number_max_gives_a_null_feed_max():
    data = feed()
    data["ranges"][0] = {"index": 0, "label": "<5%", "dots": 0}
    rows, _, _ = run(FakeResponse(200, json.dumps(data).encode()))
    p5 = [r for r in rows if r.region == "us-east-1" and r.sku == "p5.48xlarge"][0]
    assert p5.outcome == "success"
    assert (p5.details["interruption_range_max_pct"], p5.details["feed_max_pct"]) == (5, None)
