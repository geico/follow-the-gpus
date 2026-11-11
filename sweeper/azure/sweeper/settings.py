"""Settings from the app settings of the Function App."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_MAX_AGE_SECONDS = 180


class SettingsError(ValueError):
    """An app setting is missing or not valid."""


@dataclass(frozen=True)
class Settings:
    probe_rg: str
    experiment: str
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS
    # The data storage account's logs go here too (spec 0003 section 8.2, spec 0007 section
    # 9). Optional: with no value, the sweeper logs through `logging` only, as before.
    data_storage_account: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Settings:
        probe_rg = env.get("FTG_PROBE_RG", "").strip()
        if not probe_rg:
            raise SettingsError("FTG_PROBE_RG is not set")
        experiment = env.get("FTG_EXPERIMENT", "").strip()
        if not experiment:
            raise SettingsError("FTG_EXPERIMENT is not set")
        raw_age = env.get("FTG_MAX_AGE_SECONDS", "").strip() or str(DEFAULT_MAX_AGE_SECONDS)
        try:
            max_age = int(raw_age)
        except ValueError as exc:
            raise SettingsError("FTG_MAX_AGE_SECONDS must be a whole number of seconds") from exc
        if max_age < 0:
            raise SettingsError("FTG_MAX_AGE_SECONDS must be 0 or more")
        data_storage_account = env.get("FTG_DATA_STORAGE_ACCOUNT", "").strip() or None
        return cls(
            probe_rg=probe_rg,
            experiment=experiment,
            max_age_seconds=max_age,
            data_storage_account=data_storage_account,
        )
