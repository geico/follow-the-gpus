"""One sweeper run: query, decide, delete in stages, fallback, health line."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from sweeper.model import DELETE_STAGES, VM, VMSS, Resource
from sweeper.query import QueryError, build_query, parse_row
from sweeper.rules import DELETE, Decision, decide, is_young_vm
from sweeper.settings import Settings

# Seconds that a run waits for its deletes in total. Below the 5 minute functionTimeout.
DEFAULT_RUN_BUDGET_SECONDS = 240.0


class Rows(Protocol):
    """Runs 1 KQL query and returns each row as a dict."""

    def query(self, kql: str) -> list[dict[str, Any]]: ...


class Handle(Protocol):
    """A long-running operation that ARM accepted."""

    def wait(self, timeout: float) -> bool:
        """Wait up to timeout seconds. Return True when the operation is complete."""
        ...


class Deleter(Protocol):
    """Starts an ARM delete. Returns None when the resource is already gone (204 or 404)."""

    def begin_delete(self, resource: Resource) -> Handle | None: ...


class Scaler(Protocol):
    """Sets the capacity of a scale set to 0."""

    def begin_scale_to_zero(self, vmss: Resource) -> Handle: ...


class SweepError(RuntimeError):
    """The run could not query Resource Graph."""


@dataclass
class RunResult:
    scanned: int = 0
    deleted: int = 0
    errors: int = 0
    actions: dict[str, str] = field(default_factory=dict)
    scaled: list[str] = field(default_factory=list)

    @property
    def health_line(self) -> str:
        return f"sweeper run: scanned {self.scanned}, deleted {self.deleted}, errors {self.errors}"


def _age_text(age: float | None) -> str:
    return "unknown" if age is None else f"{age:.0f}"


def _log_decision(
    logger: logging.Logger, action: str, resource: Resource, age: float | None, reason: str
) -> None:
    logger.info(
        "sweeper decision: action=%s age_s=%s type=%s id=%s reason=%s",
        action,
        _age_text(age),
        resource.type,
        resource.id,
        reason,
    )


def sweep(
    settings: Settings,
    rows: Rows,
    deleter: Deleter,
    scaler: Scaler,
    *,
    now: Callable[[], datetime],
    logger: logging.Logger,
    run_budget_seconds: float = DEFAULT_RUN_BUDGET_SECONDS,
) -> RunResult:
    result = RunResult()
    kql = build_query(settings.probe_rg, settings.experiment)
    try:
        raw_rows = rows.query(kql)
    except Exception as exc:
        result.errors += 1
        logger.error("sweeper query failed: %s", exc)
        logger.info(result.health_line)
        raise SweepError("Resource Graph query failed") from exc

    result.scanned = len(raw_rows)
    resources: list[Resource] = []
    for raw in raw_rows:
        try:
            resources.append(parse_row(raw))
        except (QueryError, TypeError, ValueError) as exc:
            result.errors += 1
            logger.error("sweeper decision: action=error reason=bad row (%s)", exc)

    at = now()
    probe_in_flight = any(is_young_vm(r, at, settings.max_age_seconds) for r in resources)
    decisions: dict[str, Decision] = {}
    for resource in resources:
        decision = decide(
            resource,
            at,
            max_age_seconds=settings.max_age_seconds,
            probe_rg=settings.probe_rg,
            experiment=settings.experiment,
            probe_in_flight=probe_in_flight,
        )
        decisions[resource.id] = decision
        if decision.action != DELETE:
            result.actions[resource.id] = decision.action
            _log_decision(logger, decision.action, resource, decision.age_seconds, decision.reason)

    deadline = time.monotonic() + run_budget_seconds
    for stage in DELETE_STAGES:
        _delete_stage(stage, resources, decisions, deleter, deadline, result, logger)

    _fallback(resources, decisions, scaler, deadline, result, logger)
    logger.info(result.health_line)
    return result


def _delete_stage(
    stage: tuple[str, ...],
    resources: list[Resource],
    decisions: dict[str, Decision],
    deleter: Deleter,
    deadline: float,
    result: RunResult,
    logger: logging.Logger,
) -> None:
    started: list[tuple[Resource, Handle]] = []
    for resource in resources:
        decision = decisions[resource.id]
        if decision.action != DELETE or resource.type not in stage:
            continue
        try:
            handle = deleter.begin_delete(resource)
        except Exception as exc:
            result.errors += 1
            result.actions[resource.id] = "error"
            _log_decision(logger, "error", resource, decision.age_seconds, f"delete failed: {exc}")
            continue
        if handle is None:
            result.deleted += 1
            result.actions[resource.id] = "already gone"
            _log_decision(logger, "already gone", resource, decision.age_seconds, decision.reason)
            continue
        started.append((resource, handle))

    for resource, handle in started:
        decision = decisions[resource.id]
        try:
            done = handle.wait(max(0.0, deadline - time.monotonic()))
        except Exception as exc:
            result.errors += 1
            result.actions[resource.id] = "error"
            _log_decision(logger, "error", resource, decision.age_seconds, f"delete failed: {exc}")
            continue
        result.deleted += 1
        action = "deleted" if done else "delete accepted"
        result.actions[resource.id] = action
        _log_decision(logger, action, resource, decision.age_seconds, decision.reason)


def _fallback(
    resources: list[Resource],
    decisions: dict[str, Decision],
    scaler: Scaler,
    deadline: float,
    result: RunResult,
    logger: logging.Logger,
) -> None:
    """Set the capacity of each scale set with old instances to 0, after the deletes."""
    scale_sets = {r.id.lower(): r for r in resources if r.type == VMSS}
    old_instances: dict[str, int] = {}
    for resource in resources:
        if resource.type != VM or not resource.vmss_id:
            continue
        if decisions[resource.id].action != DELETE:
            continue
        key = resource.vmss_id.lower()
        old_instances[key] = old_instances.get(key, 0) + 1

    for key, count in sorted(old_instances.items()):
        vmss = scale_sets.get(key)
        if vmss is None:
            logger.info(
                "sweeper fallback: action=skip instances=%d id=%s reason=%s",
                count,
                key,
                "scale set is not in the query result",
            )
            continue
        try:
            handle = scaler.begin_scale_to_zero(vmss)
            done = handle.wait(max(0.0, deadline - time.monotonic()))
        except Exception as exc:
            result.errors += 1
            logger.error(
                "sweeper fallback: action=error instances=%d id=%s reason=%s", count, vmss.id, exc
            )
            continue
        result.scaled.append(vmss.id)
        logger.info(
            "sweeper fallback: action=%s instances=%d capacity_was=%s id=%s",
            "scaled to 0" if done else "scale to 0 accepted",
            count,
            vmss.capacity,
            vmss.id,
        )
