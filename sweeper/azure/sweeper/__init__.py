"""The sweeper deletes probe resources that are older than a threshold.

The logic has no Azure calls. ``azure_clients`` holds the real clients.
"""

from sweeper.model import Resource
from sweeper.run import RunResult, sweep
from sweeper.settings import Settings, SettingsError

__all__ = ["Resource", "RunResult", "Settings", "SettingsError", "sweep"]
