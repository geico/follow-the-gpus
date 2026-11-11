"""Shared fakes. No test touches the network or a cloud credential."""

import importlib
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ftg_prober.config import load_config

REPO_ROOT = Path(__file__).resolve().parents[2]
TARGETS_PATH = REPO_ROOT / "config" / "targets.yaml"
SPEC_0002_PATH = REPO_ROOT / "spec" / "0002-gpu-capacity-tracker-spec.md"


def require_cloud_extras() -> bool:
    """True when FTG_REQUIRE_CLOUD_EXTRAS is `1` or `true` (any case). `0` and `false` are off."""
    return os.environ.get("FTG_REQUIRE_CLOUD_EXTRAS", "").strip().lower() in ("1", "true")


def import_or_skip(name: str):
    """Import a cloud SDK module for a shape test.

    A plain pytest run skips the test when the module is missing. `make prober/test` sets
    FTG_REQUIRE_CLOUD_EXTRAS=1 (or `true`) after it installs both extras, so a failed import
    there is an error: a skip would hide a fault in the SDK that the shape checks compare
    the fakes with.
    """
    if require_cloud_extras():
        return importlib.import_module(name)
    return pytest.importorskip(name)


class MemoryStore:
    """Keeps each object in a dict. Refuses to overwrite, like the real stores."""

    url = "memory://"

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.puts: list[str] = []

    def put(self, key: str, data: bytes) -> None:
        if key in self.objects:
            raise FileExistsError(key)
        self.objects[key] = data
        self.puts.append(key)


class FakeClock:
    """A monotonic clock that only moves when a test sleeps."""

    def __init__(self, start: float = 0.0) -> None:
        self.t = start
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds

    def now(self) -> datetime:
        return datetime.fromtimestamp(1_760_000_000 + self.t, tz=UTC)


@pytest.fixture
def repo_config():
    return load_config(TARGETS_PATH)


@pytest.fixture
def memory_store():
    return MemoryStore()


@pytest.fixture
def clock():
    return FakeClock()
