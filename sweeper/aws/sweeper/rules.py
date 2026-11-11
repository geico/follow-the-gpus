"""The age rule of spec 0013 section 6: 1 decision per Capacity Reservation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

EXPERIMENT_TAG = "experiment"

CANCEL = "cancel"
KEEP = "keep"
SKIP = "skip"

# The states that `DescribeCapacityReservations` filters on (section 5). `pending` is listed so
# that a reservation stuck in it shows in the log every run; it cannot be cancelled.
LISTED_STATES = ("pending", "assessing", "active", "delayed")
# The states `CancelCapacityReservation` accepts with no cancellation quote (section 6). A probe
# reservation has no commitment duration, so `active` needs no quote.
# https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CancelCapacityReservation.html
CANCELLABLE_STATES = frozenset({"assessing", "active", "delayed"})


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str
    age_seconds: float | None


def tag_value(reservation: dict[str, Any], key: str) -> str | None:
    for tag in reservation.get("Tags") or []:
        if tag.get("Key") == key:
            return tag.get("Value")
    return None


def decide(
    reservation: dict[str, Any], at: datetime, *, experiment: str, max_age_seconds: int
) -> Decision:
    """The first check that applies gives the decision (spec 0013 section 6)."""
    created = reservation.get("CreateDate")
    age = None if created is None else (at - created).total_seconds()
    if tag_value(reservation, EXPERIMENT_TAG) != experiment:
        return Decision(SKIP, "experiment tag does not match", age)
    if age is None:
        return Decision(KEEP, "no CreateDate", age)
    if age <= max_age_seconds:
        return Decision(KEEP, "young", age)
    state = reservation.get("State")
    if state not in CANCELLABLE_STATES:
        return Decision(KEEP, f"state {state} cannot be cancelled", age)
    return Decision(CANCEL, f"older than {max_age_seconds} s", age)
