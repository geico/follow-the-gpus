"""The AWS sweeper cancels probe Capacity Reservations that are older than a threshold.

The logic takes an EC2 client per Region from the caller. ``handler.py`` builds the real ones.
"""

from sweeper.run import RunResult, SweepError, sweep
from sweeper.settings import Settings, SettingsError

__all__ = ["RunResult", "Settings", "SettingsError", "SweepError", "sweep"]
