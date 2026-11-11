"""`local` mode of the `aws_odcr` probe: scripted outcomes, no AWS call. Spec 0002 section 4.9.

`SimEc2` has the methods that `odcr.py` calls on a boto3 `ec2` client. The probe runs the same
code in every run mode; only the client differs. A `VirtualClock` replaces the wall clock, so
that a `pending_timeout` step reaches the probe timeout with no real wait.
"""

from typing import Any

# The sim steps. An entry's `sim_script` cycles through them, 1 step per probe call.
SIM_STEPS = (
    "success",
    "short_active",
    "capacity",
    "quota",
    "throttle",
    "pending_timeout",
    "failed",
    "cancel_fails",
)
DEFAULT_SIM_SCRIPT = ("success",)
SIM_RESERVATION_PREFIX = "cr-sim-"


class SimError(Exception):
    """Has the `response` shape of a botocore `ClientError`, so `odcr.error_code` reads it."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code, "Message": "sim"}}


class VirtualClock:
    """A clock that moves only when the probe sleeps. No real wait."""

    def __init__(self) -> None:
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def script_of(target: dict[str, Any]) -> list[str]:
    """The entry's `sim_script`, else the default script. An empty script is the default."""
    script = target.get("sim_script")
    if not script:
        return list(DEFAULT_SIM_SCRIPT)
    if isinstance(script, str):
        return [script]
    return [str(step) for step in script]


class SimEc2:
    """The fault injection client. `step` is 1 of `SIM_STEPS`; the probe sets it per call.

    `short_active` places half of the requested count. `cancel_fails` always fails the cancel
    call, so a teardown never verifies. `pending_timeout` never leaves `pending`.
    """

    def __init__(self, step: str = "success", region: str = "sim-region-1") -> None:
        self.step = step
        self.region = region
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._reservations: dict[str, dict[str, Any]] = {}
        self._counter = 0

    def _check_step(self) -> None:
        if self.step not in SIM_STEPS:
            raise SimError(f"SimUnknownStep:{self.step}")

    def describe_instance_type_offerings(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("describe_instance_type_offerings", kwargs))
        return {
            "InstanceTypeOfferings": [
                {
                    "InstanceType": (kwargs.get("Filters") or [{"Values": ["sim"]}])[0]["Values"][
                        0
                    ],
                    "LocationType": kwargs.get("LocationType", "availability-zone"),
                    "Location": zone_id,
                }
                for zone_id, _ in self._zones()
            ]
        }

    def _zones(self) -> list[tuple[str, str]]:
        """`(zone ID, zone name)` of the 3 zones of the sim Region."""
        return [(f"sim-{self.region}-az{i}", f"{self.region}{'abc'[i - 1]}") for i in (1, 2, 3)]

    def describe_availability_zones(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("describe_availability_zones", kwargs))
        wanted = kwargs.get("ZoneIds")
        return {
            "AvailabilityZones": [
                {"ZoneId": zone_id, "ZoneName": name, "ZoneType": "availability-zone"}
                for zone_id, name in self._zones()
                if not wanted or zone_id in wanted
            ]
        }

    def create_capacity_reservation(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("create_capacity_reservation", kwargs))
        self._check_step()
        if self.step == "capacity":
            raise SimError("InsufficientInstanceCapacity")
        if self.step == "quota":
            raise SimError("InstanceLimitExceeded")
        if self.step == "throttle":
            raise SimError("RequestLimitExceeded")
        self._counter += 1
        reservation_id = f"{SIM_RESERVATION_PREFIX}{self._counter:04d}"
        n = int(kwargs["InstanceCount"])
        if self.step == "pending_timeout":
            state, total = "pending", 0
        elif self.step == "failed":
            state, total = "failed", 0
        elif self.step == "short_active":
            state, total = "active", max(0, n // 2)
        else:  # success, cancel_fails
            state, total = "active", n
        self._reservations[reservation_id] = {
            "CapacityReservationId": reservation_id,
            "State": state,
            "TotalInstanceCount": total,
            "AvailableInstanceCount": total,
        }
        return {"CapacityReservation": dict(self._reservations[reservation_id])}

    def describe_capacity_reservations(self, **kwargs: Any) -> dict[str, Any]:
        """Poll by `CapacityReservationIds`, or list all (the cleanup call uses `Filters`)."""
        self.calls.append(("describe_capacity_reservations", kwargs))
        ids = kwargs.get("CapacityReservationIds")
        if ids:
            found = [dict(self._reservations[i]) for i in ids if i in self._reservations]
        else:
            found = [dict(r) for r in self._reservations.values()]
        return {"CapacityReservations": found}

    def cancel_capacity_reservation(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("cancel_capacity_reservation", kwargs))
        if self.step == "cancel_fails":
            raise SimError("RequestLimitExceeded")
        reservation_id = kwargs["CapacityReservationId"]
        reservation = self._reservations.get(reservation_id)
        if reservation is not None:
            reservation["State"] = "cancelled"
        return {"Return": True}
