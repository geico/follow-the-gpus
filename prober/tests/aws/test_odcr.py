"""`aws_odcr`: the halving search, the teardown, the cleanup. Spec 0002 section 4.9."""

import logging
from typing import Any

import pytest

from ftg_prober.aws.odcr import (
    END_DATE_MAX_SECONDS,
    OdcrProbe,
    _create_and_poll,
    odcr_settings,
    quota_code,
)
from ftg_prober.probes import Clients
from tests.aws.fakes import FakeClientError, FakeEc2, make_ctx


class FakeClock:
    """A monotonic clock that only moves when the probe sleeps. No real wait."""

    def __init__(self) -> None:
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


SETTINGS = {
    "probe_timeout_seconds": 20,
    "poll_interval_seconds": 5,
    "teardown_timeout_seconds": 20,
}


def offerings_answer(zone_ids: list[str]) -> Any:
    """The real shape of `DescribeInstanceTypeOfferings` with `LocationType` =
    `availability-zone-id`: `Location` is the zone ID (checked against the botocore model)."""
    return lambda **kwargs: {
        "InstanceTypeOfferings": [
            {
                "InstanceType": kwargs["Filters"][0]["Values"][0],
                "LocationType": "availability-zone-id",
                "Location": zone_id,
            }
            for zone_id in zone_ids
        ]
    }


def zone_names_answer(names: dict[str, str]) -> Any:
    """The real shape of `DescribeAvailabilityZones`, for the zone IDs asked for."""
    return lambda **kwargs: {
        "AvailabilityZones": [
            {"ZoneId": zone_id, "ZoneName": names[zone_id], "ZoneType": "availability-zone"}
            for zone_id in kwargs["ZoneIds"]
            if zone_id in names
        ]
    }


NAMES = {"use1-az1": "us-east-1a", "use1-az2": "us-east-1b", "use1-az4": "us-east-1c"}
ONE_ZONE = {
    "describe_instance_type_offerings": offerings_answer(["use1-az1"]),
    "describe_availability_zones": zone_names_answer(NAMES),
}


def entry(**overrides: Any) -> dict[str, Any]:
    base = {
        "instance_type": "m5.large",
        "region": "us-east-1",
        "n": 2,
    }
    base.update(overrides)
    return base


def capacity_limited(capacity: int, reservation_id: str = "cr-test-1") -> dict[str, Any]:
    """A fake EC2 whose Create/Describe/Cancel agree on 1 reservation of known capacity.

    `capacity` is the largest N that a create can fill. A smaller N succeeds in full; a
    larger N places only `capacity` and is a short fill. Lets a test drive the real halving
    search to a known answer, instead of hand-scripting every attempt's response.
    """
    state: dict[str, Any] = {"state": None, "total": None}

    def create(**kwargs: Any) -> dict[str, Any]:
        n = kwargs["InstanceCount"]
        total = min(n, capacity)
        state["state"], state["total"] = "active", total
        return {
            "CapacityReservation": {
                "CapacityReservationId": reservation_id,
                "State": "active",
                "TotalInstanceCount": total,
            }
        }

    def describe(**kwargs: Any) -> dict[str, Any]:
        if state["state"] is None:
            return {"CapacityReservations": []}
        return {
            "CapacityReservations": [
                {
                    "CapacityReservationId": reservation_id,
                    "State": state["state"],
                    "TotalInstanceCount": state["total"],
                }
            ]
        }

    def cancel(**kwargs: Any) -> dict[str, Any]:
        state["state"] = "cancelled"
        return {"Return": True}

    return {
        **ONE_ZONE,
        "create_capacity_reservation": create,
        "describe_capacity_reservations": describe,
        "cancel_capacity_reservation": cancel,
    }


class FakeServiceQuotas:
    """A fake Service Quotas client. `value` is the quota, `error` a code to raise instead."""

    def __init__(self, value: float | None = None, error: str | None = None) -> None:
        self.value, self.error = value, error
        self.calls: list[dict[str, Any]] = []

    def get_service_quota(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.error:
            raise FakeClientError(self.error, "GetServiceQuota")
        return {
            "Quota": {
                "ServiceCode": kwargs["ServiceCode"],
                "QuotaCode": kwargs["QuotaCode"],
                "QuotaName": "Running On-Demand Standard (A, C, D, H, I, M, R, T, Z) instances",
                "Value": self.value,
                "Unit": "None",
                "Adjustable": True,
                "GlobalQuota": False,
            }
        }


def instance_types(vcpus: int) -> Any:
    return lambda **_: {"InstanceTypes": [{"VCpuInfo": {"DefaultVCpus": vcpus}}]}


def run(
    ec2: FakeEc2,
    targets: list[dict[str, Any]] | None = None,
    quota: FakeServiceQuotas | None = None,
    **settings: Any,
) -> list[Any]:
    section: dict[str, Any] = {"provisioning_probes": targets or [entry()], **SETTINGS, **settings}
    ctx = make_ctx(section, clients={"us-east-1": ec2})
    probe = OdcrProbe()
    probe._client_for = lambda _ctx, _target, _mode: (ec2, FakeClock())  # noqa: SLF001
    probe._quota_client_for = lambda _ctx, _target, _sim: quota  # noqa: SLF001
    return probe.run(ctx)


def test_success_creates_cancels_and_verifies():
    ec2 = FakeEc2("us-east-1", **capacity_limited(2))
    rows = run(ec2)
    assert len(rows) == 1
    row = rows[0]
    assert (row.probe_type, row.outcome, row.n_requested, row.n_placed) == (
        "aws_odcr",
        "success",
        2,
        2,
    )
    assert row.details["teardown"]["verified"] is True
    create = next(c for c in ec2.calls if c[0] == "create_capacity_reservation")
    assert create[1]["InstanceMatchCriteria"] == "targeted"
    assert create[1]["InstancePlatform"] == "Linux/UNIX"
    assert create[1]["AvailabilityZoneId"] == "use1-az1"
    tags = {t["Key"]: t["Value"] for t in create[1]["TagSpecifications"][0]["Tags"]}
    assert tags["experiment"] == "ftg"


def test_short_fill_is_no_capacity_and_halves_then_finds_the_limit():
    ec2 = FakeEc2("us-east-1", **capacity_limited(1))
    rows = run(ec2, targets=[entry(n=4)])
    outcomes = [(r.n_requested, r.outcome, r.n_placed) for r in rows]
    # C=4 fails (capacity 1): no_capacity. Next = 2: fails. Next = 1: succeeds. Then lo=1,
    # hi=2, next=(1+2)//2=1=lo: stop.
    assert outcomes == [(4, "no_capacity", 0), (2, "no_capacity", 0), (1, "success", 1)]
    assert all(r.details["teardown"]["verified"] for r in rows)


def test_capacity_error_at_create_is_no_capacity_and_halves():
    ec2 = FakeEc2(
        "us-east-1",
        **ONE_ZONE,
        create_capacity_reservation=[FakeClientError("InsufficientInstanceCapacity")] * 3,
    )
    rows = run(ec2, targets=[entry(n=4)])
    assert [(r.n_requested, r.outcome, r.n_placed, r.error_code) for r in rows] == [
        (4, "no_capacity", 0, "InsufficientInstanceCapacity"),
        (2, "no_capacity", 0, "InsufficientInstanceCapacity"),
        (1, "no_capacity", 0, "InsufficientInstanceCapacity"),
    ]
    assert all(r.details["teardown"]["verified"] for r in rows)
    assert not any(c[0] == "cancel_capacity_reservation" for c in ec2.calls)


def test_quota_error_stops_the_search():
    ec2 = FakeEc2(
        "us-east-1",
        **ONE_ZONE,
        create_capacity_reservation=[FakeClientError("InstanceLimitExceeded")],
    )
    rows = run(ec2)
    assert len(rows) == 1
    assert (rows[0].outcome, rows[0].n_placed, rows[0].error_code) == (
        "quota",
        None,
        "InstanceLimitExceeded",
    )


def test_throttle_at_create_is_error_and_stops():
    ec2 = FakeEc2(
        "us-east-1",
        **ONE_ZONE,
        create_capacity_reservation=[FakeClientError("RequestLimitExceeded")],
    )
    rows = run(ec2)
    assert len(rows) == 1
    assert (rows[0].outcome, rows[0].error_code) == ("error", "RequestLimitExceeded")


def test_pending_at_probe_timeout_is_timeout_and_teardown_is_not_verified():
    ec2 = FakeEc2(
        "us-east-1",
        **ONE_ZONE,
        create_capacity_reservation=lambda **kwargs: {
            "CapacityReservation": {
                "CapacityReservationId": "cr-pending",
                "State": "pending",
                "TotalInstanceCount": 0,
            }
        },
        describe_capacity_reservations=lambda **kwargs: {
            "CapacityReservations": [
                {
                    "CapacityReservationId": "cr-pending",
                    "State": "pending",
                    "TotalInstanceCount": 0,
                }
            ]
        },
    )
    rows = run(ec2)
    assert len(rows) == 1
    assert rows[0].outcome == "timeout"
    assert rows[0].details["teardown"]["verified"] is False
    # AWS lists `pending` as not cancellable, but the teardown still tries: a cancel on a
    # non-cancellable state just fails and is recorded, never skipped outright.
    assert any(c[0] == "cancel_capacity_reservation" for c in ec2.calls)


def _always_raise(code: str) -> Any:
    def _raise(**_kwargs: Any) -> Any:
        raise FakeClientError(code)

    return _raise


def test_unverified_teardown_stops_the_search_even_on_no_capacity():
    """A short fill would normally halve N and continue. A cancel that never succeeds stops it."""
    cfg = capacity_limited(1)
    cfg["cancel_capacity_reservation"] = _always_raise("RequestLimitExceeded")
    ec2 = FakeEc2("us-east-1", **cfg)
    rows = run(ec2, targets=[entry(n=4)])
    assert len(rows) == 1
    assert rows[0].outcome == "no_capacity"
    assert rows[0].details["teardown"]["verified"] is False


def test_type_not_offered_is_restricted_with_no_create_call():
    ec2 = FakeEc2(
        "us-east-1", describe_instance_type_offerings=lambda **_: {"InstanceTypeOfferings": []}
    )
    rows = run(ec2)
    assert len(rows) == 1
    assert rows[0].outcome == "restricted"
    assert rows[0].details["n_config"] == 2  # parity with the Azure rows
    assert not any(c[0] == "create_capacity_reservation" for c in ec2.calls)


def test_simulated_entry_uses_the_sim_client_and_makes_no_real_call():
    section = {"provisioning_probes": [entry(simulate=True, sim_script="success")], **SETTINGS}
    ctx = make_ctx(section, clients={})
    rows = OdcrProbe().run(ctx)
    assert len(rows) == 3  # one per sim zone
    assert rows[0].outcome == "success"
    assert rows[0].simulated is True


def test_cleanup_cancels_a_stale_active_reservation_and_records_a_row():
    """A stale reservation (tagged, `active`, older than probe + teardown timeout) is cancelled
    and recorded before the configured entries run. A fresh one from this call is untouched."""
    from datetime import timedelta

    from tests.aws.fakes import NOW

    reservations: dict[str, dict[str, Any]] = {
        "cr-stale": {
            "CapacityReservationId": "cr-stale",
            "State": "active",
            "AvailabilityZone": "us-east-1a",
            "InstanceType": "m5.large",
            "CreateDate": NOW - timedelta(hours=1),
        }
    }
    counter = {"n": 0}

    def describe(**kwargs: Any) -> dict[str, Any]:
        ids = kwargs.get("CapacityReservationIds")
        if ids:
            found = [dict(reservations[i]) for i in ids if i in reservations]
            return {"CapacityReservations": found}
        return {"CapacityReservations": [dict(r) for r in reservations.values()]}

    def cancel(**kwargs: Any) -> dict[str, Any]:
        rid = kwargs["CapacityReservationId"]
        if rid in reservations:
            reservations[rid]["State"] = "cancelled"
        return {"Return": True}

    def create(**kwargs: Any) -> dict[str, Any]:
        counter["n"] += 1
        rid = f"cr-new-{counter['n']}"
        reservations[rid] = {
            "CapacityReservationId": rid,
            "State": "active",
            "TotalInstanceCount": kwargs["InstanceCount"],
        }
        return {"CapacityReservation": dict(reservations[rid])}

    ec2 = FakeEc2(
        "us-east-1",
        **ONE_ZONE,
        describe_capacity_reservations=describe,
        cancel_capacity_reservation=cancel,
        create_capacity_reservation=create,
    )
    rows = run(ec2)
    cleanup_rows = [r for r in rows if r.details.get("cleanup")]
    assert len(cleanup_rows) == 1
    assert cleanup_rows[0].outcome == "success"
    assert cleanup_rows[0].details["reservation_id"] == "cr-stale"
    assert cleanup_rows[0].simulated is False
    assert reservations["cr-stale"]["State"] == "cancelled"
    search_rows = [r for r in rows if not r.details.get("cleanup")]
    assert len(search_rows) == 1
    assert search_rows[0].outcome == "success"
    assert reservations["cr-new-1"]["State"] == "cancelled"
    # The cleanup row is not an attempt of the search: it must not share the search run_id.
    assert cleanup_rows[0].run_id != search_rows[0].run_id


def test_cleanup_runs_even_when_the_first_entry_of_a_region_is_local():
    """A region with a `local` entry and a `cloud-cpu` entry must still get a real cleanup."""
    from datetime import timedelta

    from tests.aws.fakes import NOW

    reservations: dict[str, dict[str, Any]] = {
        "cr-stale": {
            "CapacityReservationId": "cr-stale",
            "State": "active",
            "AvailabilityZone": "us-east-1a",
            "InstanceType": "m5.large",
            "CreateDate": NOW - timedelta(hours=1),
        }
    }

    def describe(**kwargs: Any) -> dict[str, Any]:
        ids = kwargs.get("CapacityReservationIds")
        if ids:
            return {
                "CapacityReservations": [dict(reservations[i]) for i in ids if i in reservations]
            }
        return {"CapacityReservations": [dict(r) for r in reservations.values()]}

    def cancel(**kwargs: Any) -> dict[str, Any]:
        reservations[kwargs["CapacityReservationId"]]["State"] = "cancelled"
        return {"Return": True}

    ec2 = FakeEc2(
        "us-east-1",
        describe_instance_type_offerings=lambda **_: {"InstanceTypeOfferings": []},
        describe_capacity_reservations=describe,
        cancel_capacity_reservation=cancel,
    )
    targets = [entry(simulate=True), entry(instance_type="g5.xlarge")]
    rows = run(ec2, targets=targets)
    cleanup_rows = [r for r in rows if r.details.get("cleanup")]
    assert len(cleanup_rows) == 1
    assert reservations["cr-stale"]["State"] == "cancelled"


def test_teardown_cancels_at_least_once_even_if_the_pending_poll_used_the_whole_timeout():
    """A reservation that turns `active` exactly as the teardown timeout runs out must still
    get 1 cancel call, not 0 (spec 0002 section 4.9, step 5: cancel an `active` reservation)."""
    from ftg_prober.aws.odcr import Settings, teardown

    calls = {"describe": 0, "cancel": 0}
    state = {"value": "pending"}

    def describe(**kwargs: Any) -> dict[str, Any]:
        calls["describe"] += 1
        if calls["describe"] == 5:
            state["value"] = "active"
        return {
            "CapacityReservations": [{"CapacityReservationId": "cr-x", "State": state["value"]}]
        }

    def cancel(**kwargs: Any) -> dict[str, Any]:
        calls["cancel"] += 1
        state["value"] = "cancelled"
        return {"Return": True}

    ec2 = FakeEc2(
        "us-east-1", describe_capacity_reservations=describe, cancel_capacity_reservation=cancel
    )
    settings = Settings(
        probe_timeout_seconds=20, poll_interval_seconds=5, teardown_timeout_seconds=20
    )
    result = teardown(ec2, FakeClock(), "cr-x", settings)
    assert calls["cancel"] == 1
    assert result["verified"] is True


def _teardown_seeing(state: str) -> tuple[dict[str, Any], list[str]]:
    from ftg_prober.aws.odcr import Settings, teardown

    calls: list[str] = []

    def describe(**kwargs: Any) -> dict[str, Any]:
        calls.append("describe")
        # The shape of the real response for a `failed` reservation (API reference,
        # CapacityReservation): CapacityReservationId, State, and the usual attributes.
        return {
            "CapacityReservations": [
                {
                    "CapacityReservationId": "cr-z",
                    "State": state,
                    "InstanceType": "p5.4xlarge",
                    "AvailabilityZone": "us-east-1a",
                    "TotalInstanceCount": 2,
                    "AvailableInstanceCount": 0,
                }
            ]
        }

    def cancel(**kwargs: Any) -> dict[str, Any]:
        calls.append("cancel")
        raise FakeClientError("IncorrectState", "CancelCapacityReservation")

    ec2 = FakeEc2(
        "us-east-1", describe_capacity_reservations=describe, cancel_capacity_reservation=cancel
    )
    settings = Settings(
        probe_timeout_seconds=20, poll_interval_seconds=5, teardown_timeout_seconds=20
    )
    clock = FakeClock()
    result = teardown(ec2, clock, "cr-z", settings, initial_state="pending")
    result["clock"] = clock.t
    return result, calls


@pytest.mark.parametrize("state", ["failed", "expired", "cancelled"])
def test_teardown_verifies_an_end_state_at_once_without_a_cancel(state):
    """`failed`, `expired`, and `cancelled` hold no capacity and bill nothing."""
    result, calls = _teardown_seeing(state)
    assert result["verified"] is True
    assert result["final_state"] == state
    assert result["errors"] == []
    assert "cancel" not in calls
    assert result["clock"] == 0


@pytest.mark.parametrize("state", ["active", "pending"])
def test_teardown_still_cancels_a_reservation_that_holds_capacity(state):
    result, calls = _teardown_seeing(state)
    assert "cancel" in calls
    assert result["verified"] is False


def test_an_ambiguous_create_that_left_a_failed_reservation_does_not_halt_the_region():
    fns = _ambiguous_fns(list_fails=False, cancel_fails=True)
    inner = fns["describe_capacity_reservations"]

    def describe(**kwargs: Any) -> dict[str, Any]:
        resp = inner(**kwargs)
        for r in resp["CapacityReservations"]:
            r["State"] = "failed"
        return resp

    fns["describe_capacity_reservations"] = describe
    ec2 = FakeEc2("us-east-1", **fns)
    run(ec2)
    assert len(_zones_created(ec2)) == 3


def test_teardown_retries_the_cancel_when_a_describe_fails_but_later_shows_active():
    """A failed describe before the cancel loop must not read as "nothing to cancel": the
    state is unknown, not confirmed non-cancellable. The loop still runs and verifies once
    later describes show `active` then `cancelled`."""
    from ftg_prober.aws.odcr import Settings, teardown

    calls = {"describe": 0}
    state = {"value": "active"}

    def describe(**kwargs: Any) -> dict[str, Any]:
        calls["describe"] += 1
        if calls["describe"] == 1:
            raise FakeClientError("RequestLimitExceeded")
        return {
            "CapacityReservations": [{"CapacityReservationId": "cr-y", "State": state["value"]}]
        }

    def cancel(**kwargs: Any) -> dict[str, Any]:
        state["value"] = "cancelled"
        return {"Return": True}

    ec2 = FakeEc2(
        "us-east-1", describe_capacity_reservations=describe, cancel_capacity_reservation=cancel
    )
    settings = Settings(
        probe_timeout_seconds=20, poll_interval_seconds=5, teardown_timeout_seconds=20
    )
    result = teardown(ec2, FakeClock(), "cr-y", settings)
    assert result["verified"] is True
    assert any("RequestLimitExceeded" in e for e in result["errors"])
    assert any(c[0] == "cancel_capacity_reservation" for c in ec2.calls)


def test_create_sends_a_fresh_client_token():
    """`ClientToken` on the create makes a retry (ours, or botocore's) idempotent. Spec:
    https://docs.aws.amazon.com/AWSEC2/latest/APIReference/Run_Instance_Idempotency.html"""
    import uuid as uuid_module

    tokens: list[str] = []

    def create(**kwargs: Any) -> dict[str, Any]:
        tokens.append(kwargs["ClientToken"])
        return {
            "CapacityReservation": {
                "CapacityReservationId": "cr-token",
                "State": "active",
                "TotalInstanceCount": kwargs["InstanceCount"],
            }
        }

    ec2 = FakeEc2(
        "us-east-1",
        **ONE_ZONE,
        create_capacity_reservation=create,
        describe_capacity_reservations=lambda **kwargs: {
            "CapacityReservations": [
                {"CapacityReservationId": kwargs["CapacityReservationIds"][0], "State": "cancelled"}
            ]
        },
        cancel_capacity_reservation=lambda **_: {"Return": True},
    )
    run(ec2)
    assert len(tokens) == 1
    uuid_module.UUID(tokens[0])  # must parse as a UUID; raises otherwise


def test_ambiguous_create_error_triggers_an_extra_cleanup_pass_and_is_an_error_row():
    """A read timeout or connection error on Create does not say whether AWS created the
    reservation (no `.response`, unlike a normal `ClientError`). The row is `error`, and the
    Region's cleanup runs again at once, in case the create did succeed server-side."""
    filter_calls = {"n": 0}

    def describe(**kwargs: Any) -> dict[str, Any]:
        if not kwargs.get("CapacityReservationIds"):
            filter_calls["n"] += 1
        return {"CapacityReservations": []}

    def create(**_kwargs: Any) -> dict[str, Any]:
        raise TimeoutError("read timed out")

    ec2 = FakeEc2(
        "us-east-1",
        **ONE_ZONE,
        describe_capacity_reservations=describe,
        create_capacity_reservation=create,
    )
    rows = run(ec2)
    main_rows = [r for r in rows if not r.details.get("cleanup")]
    assert len(main_rows) == 1
    assert main_rows[0].outcome == "error"
    assert main_rows[0].error_code == "TimeoutError"
    assert main_rows[0].details.get("ambiguous_create") is True
    # 1 cleanup pass at the start of the run, plus 1 more because the create's error is
    # ambiguous: it might have succeeded despite the client-side timeout.
    assert filter_calls["n"] == 2


def test_ambiguous_create_retries_with_the_same_client_token_and_then_succeeds():
    """The first create raises a read-timeout-shaped error (no `.response`). The retry, with
    the identical `ClientToken`, returns the reservation as if AWS's own idempotency had
    found it from the first call, and the normal poll and teardown continue."""
    tokens: list[str] = []
    create_calls = {"n": 0}

    def create(**kwargs: Any) -> dict[str, Any]:
        tokens.append(kwargs["ClientToken"])
        create_calls["n"] += 1
        if create_calls["n"] == 1:
            raise TimeoutError("read timed out")
        return {
            "CapacityReservation": {
                "CapacityReservationId": "cr-retried",
                "State": "active",
                "TotalInstanceCount": kwargs["InstanceCount"],
            }
        }

    ec2 = FakeEc2(
        "us-east-1",
        **ONE_ZONE,
        create_capacity_reservation=create,
        describe_capacity_reservations=lambda **kwargs: {
            "CapacityReservations": [
                {"CapacityReservationId": kwargs["CapacityReservationIds"][0], "State": "cancelled"}
            ]
        },
        cancel_capacity_reservation=lambda **_: {"Return": True},
    )
    rows = run(ec2)
    main_rows = [r for r in rows if not r.details.get("cleanup")]
    assert len(main_rows) == 1
    assert main_rows[0].outcome == "success"
    assert len(tokens) == 2
    assert tokens[0] == tokens[1]


def test_ambiguous_create_falls_back_to_a_targeted_cleanup_with_no_age_filter():
    """Every retry still fails ambiguously. The fallback matches only this attempt's own
    tags, with no age threshold (unlike the regular cleanup), and cancels what it finds."""
    reservations: dict[str, dict[str, Any]] = {
        "cr-just-made": {
            "CapacityReservationId": "cr-just-made",
            "State": "active",
            "AvailabilityZone": "us-east-1a",
            "InstanceType": "m5.large",
        }
    }
    targeted_filter_calls = {"n": 0}

    def describe(**kwargs: Any) -> dict[str, Any]:
        ids = kwargs.get("CapacityReservationIds")
        if ids:
            return {
                "CapacityReservations": [dict(reservations[i]) for i in ids if i in reservations]
            }
        filters = {f["Name"]: f["Values"] for f in kwargs.get("Filters") or []}
        if "tag:run_id" in filters:
            targeted_filter_calls["n"] += 1
            return {"CapacityReservations": [dict(r) for r in reservations.values()]}
        return {"CapacityReservations": []}  # the regular per-run cleanup finds nothing

    def cancel(**kwargs: Any) -> dict[str, Any]:
        reservations[kwargs["CapacityReservationId"]]["State"] = "cancelled"
        return {"Return": True}

    def create(**_kwargs: Any) -> dict[str, Any]:
        raise TimeoutError("read timed out")

    ec2 = FakeEc2(
        "us-east-1",
        **ONE_ZONE,
        describe_capacity_reservations=describe,
        cancel_capacity_reservation=cancel,
        create_capacity_reservation=create,
    )
    rows = run(ec2)
    main_rows = [r for r in rows if not r.details.get("ambiguous_create_cleanup")]
    cleanup_rows = [r for r in rows if r.details.get("ambiguous_create_cleanup")]
    assert [r.outcome for r in main_rows if r.details.get("ambiguous_create")] == ["error"]
    assert targeted_filter_calls["n"] == 1
    assert len(cleanup_rows) == 1
    assert cleanup_rows[0].outcome == "success"
    assert reservations["cr-just-made"]["State"] == "cancelled"


def test_end_date_offset_over_the_maximum_raises_before_any_create():
    ec2 = FakeEc2("us-east-1", **capacity_limited(2))
    # 600 + 300 + 60 + 2 * 5 = 970 s, over END_DATE_MAX_SECONDS (900 s).
    rows = run(ec2, probe_timeout_seconds=600, teardown_timeout_seconds=300)
    assert rows == []
    assert not any(c[0] == "create_capacity_reservation" for c in ec2.calls)


def test_end_date_offset_at_the_default_settings_is_within_the_maximum():
    ec2 = FakeEc2("us-east-1", **capacity_limited(2))
    rows = run(ec2, probe_timeout_seconds=90, teardown_timeout_seconds=60)
    assert rows[0].outcome == "success"
    sent = next(c[1] for c in ec2.calls if c[0] == "create_capacity_reservation")
    offset = (sent["EndDate"] - make_ctx({}).now()).total_seconds()
    assert offset == 220
    assert offset <= END_DATE_MAX_SECONDS


def test_the_create_is_never_called_with_an_offset_over_the_maximum():
    with pytest.raises(ValueError, match="over the maximum"):
        ctx = make_ctx({}, clients={})
        ec2 = FakeEc2("us-east-1", **capacity_limited(2))
        settings = odcr_settings({"probe_timeout_seconds": 900, "teardown_timeout_seconds": 60})
        _create_and_poll(
            ctx, ec2, ec2, FakeClock(), entry(), 1, {}, settings, False, {"id": None, "state": None}
        )
    assert not any(c[0] == "create_capacity_reservation" for c in ec2.calls)


def test_n_is_capped_at_the_quota_and_the_cap_is_in_details():
    # Quota 16 vCPUs, 4 vCPUs per instance: the cap is 4, so the first attempt asks for 4.
    ec2 = FakeEc2("us-east-1", **capacity_limited(10), describe_instance_types=instance_types(4))
    quota = FakeServiceQuotas(16.0)
    rows = run(ec2, targets=[entry(n=64)], quota=quota)
    assert [(r.n_requested, r.outcome) for r in rows] == [(4, "success")]
    assert rows[0].details["n_config"] == 64
    assert rows[0].details["n_ceiling"] == 4
    assert rows[0].details["quota_vcpus"] == 16.0
    assert rows[0].details["quota_code"] == "L-1216C47A"
    assert quota.calls == [{"ServiceCode": "ec2", "QuotaCode": "L-1216C47A"}]


def test_a_quota_above_n_does_not_raise_n():
    ec2 = FakeEc2("us-east-1", **capacity_limited(10), describe_instance_types=instance_types(4))
    rows = run(ec2, targets=[entry(n=2)], quota=FakeServiceQuotas(1152.0))
    assert [r.n_requested for r in rows] == [2]
    assert rows[0].details["n_ceiling"] == 288


def test_a_quota_below_one_instance_is_a_quota_row_with_no_create():
    ec2 = FakeEc2("us-east-1", **capacity_limited(10), describe_instance_types=instance_types(4))
    rows = run(ec2, targets=[entry(n=2)], quota=FakeServiceQuotas(0.0))
    assert [(r.outcome, r.n_requested, r.n_placed) for r in rows] == [("quota", 2, None)]
    assert rows[0].details["n_config"] == 2
    assert rows[0].details["n_ceiling"] == 0
    assert not any(c[0] == "create_capacity_reservation" for c in ec2.calls)


def test_the_quota_code_follows_the_family():
    assert quota_code("m5.large") == "L-1216C47A"
    assert quota_code("g6e.xlarge") == "L-DB2E81BA"
    assert quota_code("vt1.3xlarge") == "L-DB2E81BA"
    assert quota_code("p5.48xlarge") == "L-417A185B"
    assert quota_code("inf2.xlarge") is None
    assert quota_code("trn1.2xlarge") is None


def test_an_unreadable_quota_keeps_n_and_records_the_raw_code():
    ec2 = FakeEc2("us-east-1", **capacity_limited(10), describe_instance_types=instance_types(4))
    rows = run(ec2, targets=[entry(n=2)], quota=FakeServiceQuotas(error="AccessDeniedException"))
    assert [(r.n_requested, r.outcome) for r in rows] == [(2, "success")]
    assert rows[0].details["quota_error"] == "AccessDeniedException"


def test_cleanup_follows_next_token_pages():
    """The describe fake pages like the real API: the 2nd call must send the `NextToken`
    of the 1st response. The shape fixture rejects a renamed `NextToken` on either side."""
    from datetime import timedelta

    from tests.aws.fakes import NOW

    reservations: dict[str, dict[str, Any]] = {
        rid: {
            "CapacityReservationId": rid,
            "State": "active",
            "AvailabilityZone": "us-east-1a",
            "InstanceType": "m5.large",
            "CreateDate": NOW - timedelta(hours=1),
        }
        for rid in ("cr-page-1", "cr-page-2")
    }
    pages: list[dict[str, Any]] = []

    def describe(**kwargs: Any) -> dict[str, Any]:
        ids = kwargs.get("CapacityReservationIds")
        if ids:
            return {
                "CapacityReservations": [dict(reservations[i]) for i in ids if i in reservations]
            }
        if kwargs.get("NextToken") == "page-2":
            pages.append(kwargs)
            return {"CapacityReservations": [dict(reservations["cr-page-2"])]}
        return {
            "CapacityReservations": [dict(reservations["cr-page-1"])],
            "NextToken": "page-2",
        }

    def cancel(**kwargs: Any) -> dict[str, Any]:
        reservations[kwargs["CapacityReservationId"]]["State"] = "cancelled"
        return {"Return": True}

    ec2 = FakeEc2(
        "us-east-1",
        describe_instance_type_offerings=lambda **_: {"InstanceTypeOfferings": []},
        describe_capacity_reservations=describe,
        cancel_capacity_reservation=cancel,
    )
    run(ec2)
    assert len(pages) == 1
    assert {r["State"] for r in reservations.values()} == {"cancelled"}


def test_each_entry_is_emitted_before_the_next_and_a_stop_starts_no_new_entry():
    """Spec 0003, section 6B: the rows of an entry go to `ctx.emit` before the next entry
    makes a call, and once the process stops, no new entry starts."""
    ec2 = FakeEc2("us-east-1", **capacity_limited(2))
    targets = [entry(), entry(instance_type="m5.xlarge"), entry(instance_type="m5.2xlarge")]
    ctx = make_ctx({"provisioning_probes": targets, **SETTINGS}, clients={"us-east-1": ec2})
    emitted: list[tuple[list[str], int]] = []

    def creates() -> int:
        return sum(1 for call in ec2.calls if call[0] == "create_capacity_reservation")

    ctx.emit_fn = lambda rows: emitted.append(([row.sku for row in rows], creates()))
    ctx.stop_fn = lambda: len(emitted) >= 2
    probe = OdcrProbe()
    probe._client_for = lambda _ctx, _target, _mode: (ec2, FakeClock())  # noqa: SLF001
    probe._quota_client_for = lambda _ctx, _target, _sim: None  # noqa: SLF001
    assert probe.run(ctx) == []
    assert emitted == [(["m5.large"], 1), (["m5.xlarge"], 2)]
    assert creates() == 2


def test_a_stop_during_the_precheck_starts_no_first_attempt():
    """Spec 0003, section 6B, rule 6: a stop that is set during the entry's precheck gives no
    create call and one stop row."""
    ec2 = FakeEc2("us-east-1", **capacity_limited(1))
    ctx = make_ctx({"provisioning_probes": [entry()], **SETTINGS}, clients={"us-east-1": ec2})
    ctx.stop_fn = lambda: any(c[0] == "describe_instance_type_offerings" for c in ec2.calls)
    probe = OdcrProbe()
    probe._client_for = lambda _ctx, _target, _mode: (ec2, FakeClock())  # noqa: SLF001
    probe._quota_client_for = lambda _ctx, _target, _sim: None  # noqa: SLF001
    (row,) = probe.run(ctx)
    assert row.outcome == "error"
    assert row.details["search_stopped"] == "stop"
    assert row.details["n_config"] == 2
    assert not any("create" in c[0].lower() for c in ec2.calls)


def test_a_stop_starts_no_new_attempt_of_a_search():
    """Spec 0003, section 6B: once the process stops, the search starts no new attempt. The
    attempt in progress tears down, and its row says why the search ended."""
    ec2 = FakeEc2("us-east-1", **capacity_limited(1))  # N = 2 is a short fill: a search
    ctx = make_ctx({"provisioning_probes": [entry()], **SETTINGS}, clients={"us-east-1": ec2})
    ctx.stop_fn = lambda: any(c[0] == "cancel_capacity_reservation" for c in ec2.calls)
    probe = OdcrProbe()
    probe._client_for = lambda _ctx, _target, _mode: (ec2, FakeClock())  # noqa: SLF001
    probe._quota_client_for = lambda _ctx, _target, _sim: None  # noqa: SLF001
    (row,) = probe.run(ctx)
    assert (row.outcome, row.n_requested, row.details["search_step"]) == ("no_capacity", 2, 1)
    assert row.details["teardown"]["verified"] is True
    assert row.details["search_stopped"] == "stop"
    assert sum(1 for c in ec2.calls if c[0] == "create_capacity_reservation") == 1


# --- The upward search of ADR 0023: `n_max`. ---


def creates(ec2: FakeEc2) -> list[int]:
    return [c[1]["InstanceCount"] for c in ec2.calls if c[0] == "create_capacity_reservation"]


def test_a_full_success_goes_up_by_doubling_to_n_max():
    ec2 = FakeEc2("us-east-1", **capacity_limited(100))
    rows = run(ec2, targets=[entry(n=2, n_max=16)])
    assert [(r.n_requested, r.outcome, r.n_placed) for r in rows] == [
        (2, "success", 2),
        (4, "success", 4),
        (8, "success", 8),
        (16, "success", 16),
    ]
    assert creates(ec2) == [2, 4, 8, 16]
    assert [r.details["search_step"] for r in rows] == [1, 2, 3, 4]
    # Only the rows above C carry the direction and n_max.
    assert ["search_direction" in r.details for r in rows] == [False, True, True, True]
    assert [r.details.get("n_max") for r in rows] == [None, 16, 16, 16]
    assert {r.details["search_direction"] for r in rows[1:]} == {"up"}
    assert all(r.details["teardown"]["verified"] for r in rows)
    # Each step is torn down before the next one is created.
    names = [
        c[0]
        for c in ec2.calls
        if c[0] in ("create_capacity_reservation", "cancel_capacity_reservation")
    ]
    assert names == ["create_capacity_reservation", "cancel_capacity_reservation"] * 4


def test_the_last_step_is_capped_at_n_max():
    ec2 = FakeEc2("us-east-1", **capacity_limited(100))
    rows = run(ec2, targets=[entry(n=3, n_max=10)])
    assert [r.n_requested for r in rows] == [3, 6, 10]


def test_going_up_stops_at_the_first_failure_with_no_bisection():
    ec2 = FakeEc2("us-east-1", **capacity_limited(5))
    rows = run(ec2, targets=[entry(n=2, n_max=16)])
    assert [(r.n_requested, r.outcome) for r in rows] == [
        (2, "success"),
        (4, "success"),
        (8, "no_capacity"),
    ]
    assert rows[-1].details["search_direction"] == "up"
    assert creates(ec2) == [2, 4, 8]


def test_a_failure_at_c_halves_and_never_goes_up():
    ec2 = FakeEc2("us-east-1", **capacity_limited(1))
    rows = run(ec2, targets=[entry(n=4, n_max=16)])
    assert [(r.n_requested, r.outcome) for r in rows] == [
        (4, "no_capacity"),
        (2, "no_capacity"),
        (1, "success"),
    ]
    assert not any("search_direction" in r.details for r in rows)


def test_the_quota_ceiling_below_n_max_caps_the_steps():
    ec2 = FakeEc2("us-east-1", **capacity_limited(100), describe_instance_types=instance_types(4))
    rows = run(ec2, targets=[entry(n=2, n_max=16)], quota=FakeServiceQuotas(40.0))  # ceiling 10
    assert [r.n_requested for r in rows] == [2, 4, 8, 10]


def test_a_ceiling_at_c_makes_no_step_up():
    ec2 = FakeEc2("us-east-1", **capacity_limited(100), describe_instance_types=instance_types(4))
    rows = run(ec2, targets=[entry(n=2, n_max=16)], quota=FakeServiceQuotas(8.0))  # ceiling 2
    assert [r.n_requested for r in rows] == [2]


def test_an_unreadable_quota_leaves_n_max_as_the_cap():
    ec2 = FakeEc2("us-east-1", **capacity_limited(100), describe_instance_types=instance_types(4))
    quota = FakeServiceQuotas(error="AccessDeniedException")
    rows = run(ec2, targets=[entry(n=2, n_max=8)], quota=quota)
    assert [r.n_requested for r in rows] == [2, 4, 8]


def test_an_entry_without_n_max_gives_todays_rows():
    ec2 = FakeEc2("us-east-1", **capacity_limited(100))
    rows = run(ec2, targets=[entry(n=2)])
    assert [r.n_requested for r in rows] == [2]
    assert "search_direction" not in rows[0].details
    assert "n_max" not in rows[0].details


def test_an_unverified_teardown_stops_the_search_going_up():
    cfg = capacity_limited(100)
    cfg["cancel_capacity_reservation"] = _always_raise("RequestLimitExceeded")
    ec2 = FakeEc2("us-east-1", **cfg)
    rows = run(ec2, targets=[entry(n=2, n_max=16)])
    assert [(r.n_requested, r.outcome) for r in rows] == [(2, "success")]
    assert rows[0].details["teardown"]["verified"] is False


def test_a_stop_starts_no_step_up():
    ec2 = FakeEc2("us-east-1", **capacity_limited(100))
    ctx = make_ctx(
        {"provisioning_probes": [entry(n=2, n_max=16)], **SETTINGS}, clients={"us-east-1": ec2}
    )
    ctx.stop_fn = lambda: any(c[0] == "cancel_capacity_reservation" for c in ec2.calls)
    probe = OdcrProbe()
    probe._client_for = lambda _ctx, _target, _mode: (ec2, FakeClock())  # noqa: SLF001
    probe._quota_client_for = lambda _ctx, _target, _sim: None  # noqa: SLF001
    (row,) = probe.run(ctx)
    assert (row.outcome, row.n_requested) == ("success", 2)
    assert row.details["search_stopped"] == "stop"
    assert creates(ec2) == [2]


def test_the_simulated_client_supports_the_steps_up():
    section = {
        "provisioning_probes": [entry(simulate=True, sim_script="success", n_max=16)],
        **SETTINGS,
    }
    rows = OdcrProbe().run(make_ctx(section, clients={}))
    assert [(r.n_requested, r.outcome) for r in rows] == [
        (2, "success"),
        (4, "success"),
        (8, "success"),
        (16, "success"),
    ] * 3  # 3 sim zones
    assert all(r.simulated for r in rows)


# --- The search deadline: the entry budget of ADR 0023, as the Azure probes. ---


def _timed_run(
    target: dict[str, Any], capacity: int, create_seconds: float, precheck_seconds: float = 0.0
) -> tuple[list[Any], FakeEc2, FakeClock]:
    """A real entry on a fake clock: each create call takes `create_seconds`, the precheck
    `precheck_seconds`. Returns the rows, the fake EC2, and the clock."""
    clock = FakeClock()
    fns = capacity_limited(capacity)
    create = fns["create_capacity_reservation"]
    offerings = fns["describe_instance_type_offerings"]

    def slow_create(**kwargs: Any) -> dict[str, Any]:
        clock.t += create_seconds
        return create(**kwargs)

    def slow_offerings(**kwargs: Any) -> dict[str, Any]:
        clock.t += precheck_seconds
        return offerings(**kwargs)

    fns["create_capacity_reservation"] = slow_create
    fns["describe_instance_type_offerings"] = slow_offerings
    ec2 = FakeEc2("us-east-1", **fns)
    ctx = make_ctx({"provisioning_probes": [target], **SETTINGS}, clients={"us-east-1": ec2})
    probe = OdcrProbe()
    probe._client_for = lambda _ctx, _target, _mode: (ec2, clock)  # noqa: SLF001
    probe._quota_client_for = lambda _ctx, _target, _sim: None  # noqa: SLF001
    return probe.run(ctx), ec2, clock


def test_the_search_stops_before_an_attempt_that_cannot_finish_by_the_deadline():
    """The budget is (1 + 3) x (20 + 20) = 160 s. Each create takes 50 s: after 2 attempts the
    window is 160 - 100 - 20 = 40 s, shorter than the longest probe (50 s), so no 3rd create
    starts. The rows so far are returned, the last one says why, and its teardown is verified."""
    rows, ec2, clock = _timed_run(entry(n=2, n_max=16), capacity=64, create_seconds=50)
    assert [r.n_requested for r in rows] == [2, 4]
    assert rows[-1].details["search_stopped"] == "deadline"
    assert "search_stopped" not in rows[0].details
    assert all(r.details["teardown"]["verified"] for r in rows)
    assert sum(1 for c in ec2.calls if c[0] == "create_capacity_reservation") == 2
    assert sum(1 for c in ec2.calls if c[0] == "cancel_capacity_reservation") == 2
    assert clock.t + SETTINGS["teardown_timeout_seconds"] <= 160 + 50  # inside the budget


def test_a_search_that_has_time_runs_to_its_end_and_writes_no_deadline_key():
    rows, _, _ = _timed_run(entry(n=2, n_max=16), capacity=64, create_seconds=1)
    assert [r.n_requested for r in rows] == [2, 4, 8, 16]
    assert all("search_stopped" not in r.details for r in rows)


def test_the_attempt_probe_window_is_the_time_left_minus_the_teardown_timeout():
    """A pending reservation polls only for the window. The offerings call (15 s here) runs once
    per target, before the zone budgets, so it takes nothing from the window of 20 s:
    the window is 40 - 0 - 20 = 20 s, 4 polls."""
    clock = FakeClock()
    state = {"n": 0}

    def create(**_: Any) -> dict[str, Any]:
        state["n"] += 1
        return {"CapacityReservation": {"CapacityReservationId": "cr-p", "State": "pending"}}

    def offerings(**kwargs: Any) -> dict[str, Any]:
        clock.t += 15
        return ONE_ZONE["describe_instance_type_offerings"](**kwargs)

    ec2 = FakeEc2(
        "us-east-1",
        describe_instance_type_offerings=offerings,
        describe_availability_zones=ONE_ZONE["describe_availability_zones"],
        create_capacity_reservation=create,
        describe_capacity_reservations=lambda **_: {
            "CapacityReservations": [{"CapacityReservationId": "cr-p", "State": "pending"}]
        },
        cancel_capacity_reservation=lambda **_: {"Return": True},
    )
    ctx = make_ctx({"provisioning_probes": [entry()], **SETTINGS}, clients={"us-east-1": ec2})
    probe = OdcrProbe()
    probe._client_for = lambda _ctx, _target, _mode: (ec2, clock)  # noqa: SLF001
    probe._quota_client_for = lambda _ctx, _target, _sim: None  # noqa: SLF001
    (row,) = probe.run(ctx)
    assert row.outcome == "timeout"
    assert row.details["polls"] == 4


def test_a_simulated_entry_never_writes_search_stopped_when_the_process_stops():
    """Spec 0010 section 5: the stop row of a simulated entry has no `search_stopped`."""
    section = {"provisioning_probes": [entry(simulate=True, sim_script="success")], **SETTINGS}
    ctx = make_ctx(section)
    calls = []
    ctx.stop_fn = lambda: bool(calls.append(1)) or len(calls) > 1  # false once, then true
    rows = OdcrProbe().run(ctx)
    assert [(r.simulated, r.outcome, r.details["reason"]) for r in rows] == [
        (True, "error", "stop")
    ]  # the first zone writes the stop row; no other zone starts
    assert "search_stopped" not in rows[0].details


def test_a_client_build_failure_gives_an_error_row_per_entry_and_is_not_cached():
    """A boto3 factory that raises writes 1 `error` row per entry with the exception type."""
    builds: list[str] = []

    def broken(service: str, region: str, **_: Any) -> Any:
        builds.append(service)
        raise RuntimeError("secret store unreachable")

    ctx = make_ctx({"provisioning_probes": [entry(), entry(instance_type="m5.xlarge")], **SETTINGS})
    ctx.clients = Clients(boto3_factory=broken)
    rows = OdcrProbe().run(ctx)
    assert [r.sku for r in rows] == ["m5.large", "m5.xlarge"]
    assert {r.outcome for r in rows} == {"error"}
    assert {r.error_code for r in rows} == {"RuntimeError"}
    assert {r.details["reason"] for r in rows} == {"Client build failed"}
    assert len(builds) == 2  # each entry builds again


def test_a_client_error_build_failure_keeps_the_raw_code_and_logs_the_raw_message(caplog):
    message = "You can't do that: this role isn't allowed"

    def broken(service: str, region: str, **_: Any) -> Any:
        raise FakeClientError("AccessDenied", message=message)

    ctx = make_ctx({"provisioning_probes": [entry()], **SETTINGS})
    ctx.clients = Clients(boto3_factory=broken)
    with caplog.at_level(logging.WARNING):
        rows = OdcrProbe().run(ctx)
    assert [r.error_code for r in rows] == ["AccessDenied"]
    assert message not in repr(rows)
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "odcr.client_failed" in text and "code=AccessDenied" in text
    assert repr(message) in text or message in text


def test_one_regions_build_failure_leaves_another_regions_entry_running():
    ec2 = FakeEc2("us-west-2", **capacity_limited(2))

    def factory(service: str, region: str, **_: Any) -> Any:
        if region == "us-east-1":
            raise RuntimeError("no endpoint")
        return ec2 if service == "ec2" else FakeServiceQuotas()

    targets = [entry(), entry(region="us-west-2")]
    ctx = make_ctx({"provisioning_probes": targets, **SETTINGS})
    ctx.clients = Clients(boto3_factory=factory)
    probe = OdcrProbe()
    probe._quota_client_for = lambda _ctx, _target, _sim: None  # noqa: SLF001
    rows = probe.run(ctx)
    by_region = {r.region: r.outcome for r in rows if not r.details.get("cleanup")}
    assert by_region["us-east-1"] == "error"
    assert by_region["us-west-2"] != "error"


def test_a_create_client_build_failure_comes_after_the_cleanup():
    ec2 = FakeEc2("us-east-1", **capacity_limited(2))

    def broken_create(*_: Any) -> Any:
        raise RuntimeError("create client failed")

    ctx = make_ctx({"provisioning_probes": [entry()], **SETTINGS}, clients={"us-east-1": ec2})
    probe = OdcrProbe()
    probe._create_client_for = broken_create  # noqa: SLF001
    rows = probe.run(ctx)
    assert [r.outcome for r in rows] == ["error"]
    assert rows[0].details["reason"] == "Client build failed"
    assert any(call[0] == "describe_capacity_reservations" for call in ec2.calls)


# --- Every zone that offers the type, by zone ID. ---


def three_zones(capacity: int = 2) -> dict[str, Any]:
    fns = capacity_limited(capacity)
    fns["describe_instance_type_offerings"] = offerings_answer(["use1-az4", "use1-az1", "use1-az2"])
    return fns


def test_the_offerings_call_asks_for_zone_ids_and_the_names_come_from_the_zone_call():
    ec2 = FakeEc2("us-east-1", **three_zones())
    run(ec2)
    offerings = [c for c in ec2.calls if c[0] == "describe_instance_type_offerings"]
    assert len(offerings) == 1
    assert offerings[0][1]["LocationType"] == "availability-zone-id"
    assert offerings[0][1]["Filters"] == [{"Name": "instance-type", "Values": ["m5.large"]}]
    (zone_call,) = [c for c in ec2.calls if c[0] == "describe_availability_zones"]
    assert zone_call[1]["ZoneIds"] == ["use1-az1", "use1-az2", "use1-az4"]


def test_one_probe_per_zone_each_with_the_zone_id_in_the_create_and_the_row():
    ec2 = FakeEc2("us-east-1", **three_zones())
    rows = run(ec2)
    creates = [c[1] for c in ec2.calls if c[0] == "create_capacity_reservation"]
    assert [c["AvailabilityZoneId"] for c in creates] == ["use1-az1", "use1-az2", "use1-az4"]
    assert all("AvailabilityZone" not in c for c in creates)
    assert [(r.zone, r.details["zone_id"]) for r in rows] == [
        ("us-east-1a", "use1-az1"),
        ("us-east-1b", "use1-az2"),
        ("us-east-1c", "use1-az4"),
    ]
    assert all(r.outcome == "success" for r in rows)


def test_the_reservation_tag_is_unique_per_zone_and_names_the_zone_id():
    ec2 = FakeEc2("us-east-1", **three_zones())
    run(ec2)
    tags = [
        {t["Key"]: t["Value"] for t in c[1]["TagSpecifications"][0]["Tags"]}
        for c in ec2.calls
        if c[0] == "create_capacity_reservation"
    ]
    assert [t["target"] for t in tags] == [
        "m5.large-use1-az1",
        "m5.large-use1-az2",
        "m5.large-use1-az4",
    ]


def test_n_n_max_and_the_halving_apply_to_each_zone_on_its_own():
    ec2 = FakeEc2("us-east-1", **three_zones(capacity=1))  # N = 4 is a short fill in each zone
    rows = run(ec2, targets=[entry(n=4)])
    by_zone: dict[str, list[int]] = {}
    for r in rows:
        by_zone.setdefault(r.details["zone_id"], []).append(r.n_requested)
    assert by_zone == {"use1-az1": [4, 2, 1], "use1-az2": [4, 2, 1], "use1-az4": [4, 2, 1]}


def test_a_type_that_no_zone_offers_is_one_region_row_with_no_zone():
    ec2 = FakeEc2("us-east-1", describe_instance_type_offerings=offerings_answer([]))
    (row,) = run(ec2)
    assert row.outcome == "restricted"
    assert row.zone is None and row.details["zone_id"] is None
    assert not any(c[0] == "describe_availability_zones" for c in ec2.calls)


def test_a_failed_offerings_call_is_one_error_row_with_the_raw_code():
    ec2 = FakeEc2(
        "us-east-1", describe_instance_type_offerings=_always_raise("UnauthorizedOperation")
    )
    (row,) = run(ec2)
    assert (row.outcome, row.error_code, row.details["skipped"]) == (
        "error",
        "UnauthorizedOperation",
        True,
    )


def test_a_zone_id_with_no_name_in_the_account_is_left_out():
    fns = three_zones()
    fns["describe_availability_zones"] = zone_names_answer({"use1-az1": "us-east-1a"})
    rows = run(FakeEc2("us-east-1", **fns))
    assert [r.details["zone_id"] for r in rows] == ["use1-az1"]


def test_a_simulated_entry_probes_every_sim_zone_with_its_own_script_position():
    section = {"provisioning_probes": [entry(simulate=True, sim_script="success")], **SETTINGS}
    rows = OdcrProbe().run(make_ctx(section, clients={}))
    assert [r.details["zone_id"] for r in rows] == [
        "sim-us-east-1-az1",
        "sim-us-east-1-az2",
        "sim-us-east-1-az3",
    ]
    assert [r.zone for r in rows] == ["us-east-1a", "us-east-1b", "us-east-1c"]


def test_the_sweeper_rule_matches_a_reservation_of_any_zone_by_the_experiment_tag():
    """The sweeper reads the `experiment` tag only (spec 0013), so the zone in `target` does not
    change what it finds. Prove it on the tags the probe writes for 2 zones."""
    from ftg_prober.aws.odcr import probe_tags, target_tag

    for zone_id in ("use1-az1", "use1-az6"):
        tags = probe_tags("ftg", target_tag({"instance_type": "m5.large", "zone_id": zone_id}), "r")
        assert tags["experiment"] == "ftg" and tags["target"] == f"m5.large-{zone_id}"


def test_the_zone_expanded_lane_bound_logs_a_warning_and_never_refuses(caplog):
    """3 zones x a budget of 40 s (20 + 20, no n_max) is 120 s; a cadence of 100 s
    is below it. The probe warns once for the Region and still probes every zone."""
    import logging

    ec2 = FakeEc2("us-east-1", **three_zones())
    with caplog.at_level(logging.WARNING):
        section = {"provisioning_probes": [entry()], "cadence": {"aws_odcr": "100s"}, **SETTINGS}
        ctx = make_ctx(section, clients={"us-east-1": ec2})
        probe = OdcrProbe()
        probe._client_for = lambda _ctx, _target, _mode: (ec2, FakeClock())  # noqa: SLF001
        probe._quota_client_for = lambda _ctx, _target, _sim: None  # noqa: SLF001
        rows = probe.run(ctx)
    warned = [
        r.getMessage() for r in caplog.records if "lane_bound_exceeds_cadence" in r.getMessage()
    ]
    assert len(warned) == 1 and "bound_seconds=120" in warned[0] and "region=us-east-1" in warned[0]
    assert len(rows) == 3


def test_the_zone_expanded_lane_bound_is_silent_under_the_cadence(caplog):
    import logging

    ec2 = FakeEc2("us-east-1", **three_zones())
    with caplog.at_level(logging.WARNING):
        run(ec2)  # the default cadence is 2 h
    assert not [r for r in caplog.records if "lane_bound" in r.getMessage()]


def test_an_unverified_teardown_halts_the_other_zones_and_entries_of_the_region(caplog):
    """ADR 0009 step 8: 3 zones, a cancel that always fails: exactly 1 create in the cycle."""
    import logging

    fns = three_zones()
    fns["cancel_capacity_reservation"] = _always_raise("RequestLimitExceeded")
    ec2 = FakeEc2("us-east-1", **fns)
    with caplog.at_level(logging.WARNING):
        rows = run(ec2, targets=[entry(), entry(instance_type="g5.xlarge")])
    assert sum(1 for c in ec2.calls if c[0] == "create_capacity_reservation") == 1
    assert rows[-1].details["teardown"]["verified"] is False
    assert len([r for r in caplog.records if "odcr.region_halted" in r.getMessage()]) == 2


def test_a_simulated_unverified_teardown_does_not_halt_the_real_entries_of_the_region():
    """A simulated entry (cancel_fails) opens no real reservation, so a real entry
    of the same Region still runs in the cycle."""
    ec2 = FakeEc2("us-east-1", **capacity_limited(2))
    sim = entry(instance_type="g5.xlarge", simulate=True, sim_script="cancel_fails")
    section = {"provisioning_probes": [sim, entry()], **SETTINGS}
    ctx = make_ctx(section, clients={"us-east-1": ec2})
    probe = OdcrProbe()
    real_client_for = probe._client_for  # noqa: SLF001
    probe._client_for = lambda c, t, s: (  # noqa: SLF001
        real_client_for(c, t, s) if s else (ec2, FakeClock())
    )
    probe._quota_client_for = lambda _ctx, _target, _sim: None  # noqa: SLF001
    rows = probe.run(ctx)
    assert any(not r.simulated and r.outcome == "success" for r in rows)
    assert any(c[0] == "create_capacity_reservation" for c in ec2.calls)


def test_zone_ids_with_no_name_are_a_distinct_row_and_a_warning(caplog):
    import logging

    fns = three_zones()
    fns["describe_availability_zones"] = zone_names_answer({})
    with caplog.at_level(logging.WARNING):
        (row,) = run(FakeEc2("us-east-1", **fns))
    assert row.details["reason"] == (
        "the zone IDs that offer the type have no zone name in this account"
    )
    assert any("odcr.zone_without_name" in r.getMessage() for r in caplog.records)


def test_the_offerings_call_follows_next_token():
    pages = {
        None: {
            "InstanceTypeOfferings": offerings_answer(["use1-az1"])(
                Filters=[{"Values": ["m5.large"]}]
            )["InstanceTypeOfferings"],
            "NextToken": "t2",
        },
        "t2": offerings_answer(["use1-az2"])(Filters=[{"Values": ["m5.large"]}]),
    }
    fns = capacity_limited(2)
    fns["describe_instance_type_offerings"] = lambda **kw: pages[kw.get("NextToken")]
    rows = run(FakeEc2("us-east-1", **fns))
    assert [r.details["zone_id"] for r in rows] == ["use1-az1", "use1-az2"]


def test_the_lane_bound_warning_is_logged_once_per_region(caplog):
    import logging

    ec2 = FakeEc2("us-east-1", **three_zones())
    with caplog.at_level(logging.WARNING):
        section = {
            "provisioning_probes": [entry(), entry(instance_type="g5.xlarge")],
            "cadence": {"aws_odcr": "100s"},
            **SETTINGS,
        }
        probe = OdcrProbe()
        probe._client_for = lambda _ctx, _target, _mode: (ec2, FakeClock())  # noqa: SLF001
        probe._quota_client_for = lambda _ctx, _target, _sim: None  # noqa: SLF001
        probe.run(make_ctx(section, clients={"us-east-1": ec2}))
    assert len([r for r in caplog.records if "lane_bound_exceeds" in r.getMessage()]) == 1


def test_a_stop_between_zones_logs_odcr_stopped_once(caplog):
    import logging

    ec2 = FakeEc2("us-east-1", **three_zones())
    ctx = make_ctx({"provisioning_probes": [entry()], **SETTINGS}, clients={"us-east-1": ec2})
    ctx.stop_fn = lambda: any(c[0] == "create_capacity_reservation" for c in ec2.calls)
    probe = OdcrProbe()
    probe._client_for = lambda _ctx, _target, _mode: (ec2, FakeClock())  # noqa: SLF001
    probe._quota_client_for = lambda _ctx, _target, _sim: None  # noqa: SLF001
    with caplog.at_level(logging.INFO):
        probe.run(ctx)
    stopped = [r.getMessage() for r in caplog.records if "odcr.stopped" in r.getMessage()]
    assert len(stopped) == 1 and "zones_not_run=2" in stopped[0]


def _zones_created(ec2: FakeEc2) -> set[str]:
    return {c[1]["AvailabilityZoneId"] for c in ec2.calls if c[0] == "create_capacity_reservation"}


def _ambiguous_fns(list_fails: bool, cancel_fails: bool) -> dict[str, Any]:
    reservation = {
        "CapacityReservationId": "cr-x",
        "State": "active",
        "AvailabilityZone": "us-east-1a",
        "InstanceType": "m5.large",
    }

    def describe(**kwargs: Any) -> dict[str, Any]:
        if list_fails and not kwargs.get("CapacityReservationIds"):
            filters = {f["Name"] for f in kwargs.get("Filters") or []}
            if "tag:run_id" in filters:
                raise FakeClientError("RequestLimitExceeded", "DescribeCapacityReservations")
        ids = kwargs.get("CapacityReservationIds")
        if ids or "tag:run_id" in {f["Name"] for f in kwargs.get("Filters") or []}:
            return {"CapacityReservations": [dict(reservation)]}
        return {"CapacityReservations": []}

    def create(**_: Any) -> dict[str, Any]:
        raise TimeoutError("read timed out")

    fns = {**three_zones(), "describe_capacity_reservations": describe}
    fns["create_capacity_reservation"] = create
    if cancel_fails:
        fns["cancel_capacity_reservation"] = _always_raise("RequestLimitExceeded")
    return fns


def test_an_ambiguous_create_whose_cancel_fails_halts_the_region():
    ec2 = FakeEc2("us-east-1", **_ambiguous_fns(list_fails=False, cancel_fails=True))
    run(ec2)
    assert len(_zones_created(ec2)) == 1


def test_an_ambiguous_create_whose_list_fails_halts_the_region(caplog):
    import logging

    ec2 = FakeEc2("us-east-1", **_ambiguous_fns(list_fails=True, cancel_fails=False))
    with caplog.at_level(logging.WARNING):
        run(ec2)
    assert len(_zones_created(ec2)) == 1
    assert any("list of its reservation failed" in r.getMessage() for r in caplog.records)


def test_an_exception_in_a_zone_halts_the_region():
    fns = three_zones()
    fns["create_capacity_reservation"] = lambda **_: {}  # no CapacityReservation: a KeyError
    ec2 = FakeEc2("us-east-1", **fns)
    run(ec2, targets=[entry(), entry(instance_type="g5.xlarge")])
    assert len([c for c in ec2.calls if c[0] == "create_capacity_reservation"]) == 1


def test_the_unnamed_zones_row_is_a_valid_error_row():
    fns = three_zones()
    fns["describe_availability_zones"] = zone_names_answer({})
    (row,) = run(FakeEc2("us-east-1", **fns))
    assert row.outcome == "error"
    assert row.validate() == []
