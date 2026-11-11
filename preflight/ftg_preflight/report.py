"""Findings, the report text, and the exit code."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

EXIT_OK = 0
EXIT_GAP = 1
EXIT_USAGE = 2


class Level(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass(frozen=True)
class Finding:
    """1 check result. FAIL blocks the deploy. WARN: a signal gap, or a check that cannot run."""

    level: Level
    check: str
    message: str
    fix: str = ""


def ok(check: str, message: str) -> Finding:
    return Finding(Level.PASS, check, message)


def warn(check: str, message: str, fix: str = "") -> Finding:
    return Finding(Level.WARN, check, message, fix)


def fail(check: str, message: str, fix: str = "") -> Finding:
    return Finding(Level.FAIL, check, message, fix)


def render(findings: Iterable[Finding]) -> str:
    """1 line per finding, then an indented fix line, then a summary line."""
    findings = list(findings)
    lines = []
    for f in findings:
        lines.append(f"{f.level.value}  {f.check}: {f.message}")
        if f.fix and f.level is not Level.PASS:
            lines.append(f"      fix: {f.fix}")
    counts = {lvl: sum(1 for f in findings if f.level is lvl) for lvl in Level}
    lines.append(
        f"{counts[Level.PASS]} passed, {counts[Level.WARN]} warnings, {counts[Level.FAIL]} failures"
    )
    return "\n".join(lines)


def exit_code(findings: Iterable[Finding]) -> int:
    return EXIT_GAP if any(f.level is Level.FAIL for f in findings) else EXIT_OK
