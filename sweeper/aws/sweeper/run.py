"""One sweeper run: for each Region, list, decide, cancel; then 1 health line (spec 0013)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sweeper.rules import CANCEL, LISTED_STATES, decide
from sweeper.settings import Settings


class SweepError(RuntimeError):
    """At least 1 Region could not be listed, or 1 cancel failed. The run still did the rest."""


@dataclass
class RunResult:
    scanned: int = 0
    cancelled: int = 0
    errors: int = 0
    actions: dict[str, str] = field(default_factory=dict)

    @property
    def health_line(self) -> str:
        return (
            f"sweeper run: scanned {self.scanned}, cancelled {self.cancelled}, errors {self.errors}"
        )


def list_reservations(ec2: Any, experiment: str) -> list[dict[str, Any]]:
    """Every reservation with the experiment tag in a listed state, all pages (section 5)."""
    found: list[dict[str, Any]] = []
    token: str | None = None
    while True:
        kwargs: dict[str, Any] = {
            "Filters": [
                {"Name": "tag:experiment", "Values": [experiment]},
                {"Name": "state", "Values": list(LISTED_STATES)},
            ]
        }
        if token:
            kwargs["NextToken"] = token
        resp = ec2.describe_capacity_reservations(**kwargs)
        found.extend(resp.get("CapacityReservations") or [])
        token = resp.get("NextToken")
        if not token:
            return found


def _age_text(age: float | None) -> str:
    return "unknown" if age is None else f"{age:.0f}"


def sweep(
    settings: Settings,
    ec2_for: Callable[[str], Any],
    *,
    now: Callable[[], datetime],
    logger: logging.Logger,
) -> RunResult:
    result = RunResult()
    failed_regions: list[str] = []
    failed_cancels: list[str] = []
    for region in settings.regions:
        try:
            ec2 = ec2_for(region)
            reservations = list_reservations(ec2, settings.experiment)
        except Exception as exc:  # noqa: BLE001 - 1 Region's failure never stops the others
            result.errors += 1
            failed_regions.append(region)
            logger.error("sweeper query failed: region=%s error=%s", region, exc)
            continue
        result.scanned += len(reservations)
        at = now()
        for reservation in reservations:
            rid = reservation.get("CapacityReservationId", "unknown")
            decision = decide(
                reservation,
                at,
                experiment=settings.experiment,
                max_age_seconds=settings.max_age_seconds,
            )
            action, reason = decision.action, decision.reason
            if action == CANCEL:
                try:
                    resp = ec2.cancel_capacity_reservation(CapacityReservationId=rid)
                    if resp.get("Return") is not True:
                        raise RuntimeError(f"CancelCapacityReservation returned {resp!r}")
                    action = "cancelled"
                    result.cancelled += 1
                except Exception as exc:  # noqa: BLE001 - the next run tries again
                    action, reason = "error", f"cancel failed: {exc}"
                    failed_cancels.append(rid)
                    result.errors += 1
            result.actions[rid] = action
            logger.info(
                "sweeper decision: action=%s age_s=%s region=%s state=%s id=%s reason=%s",
                action,
                _age_text(decision.age_seconds),
                region,
                reservation.get("State"),
                rid,
                reason,
            )
    logger.info(result.health_line)
    problems: list[str] = []
    if failed_regions:
        problems.append(f"DescribeCapacityReservations failed in {', '.join(failed_regions)}")
    if failed_cancels:
        problems.append(f"CancelCapacityReservation failed for {', '.join(failed_cancels)}")
    if problems:
        raise SweepError("; ".join(problems))
    return result
