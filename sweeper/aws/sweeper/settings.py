"""Settings from the environment variables of the Lambda function (spec 0013 section 4)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_MAX_AGE_SECONDS = 180

# A commercial AWS Region name, for example us-east-1 or ap-southeast-2.
_REGION_RE = re.compile(r"^[a-z]{2}(-[a-z]+)+-\d+$")


class SettingsError(ValueError):
    """An environment variable is missing or not valid."""


@dataclass(frozen=True)
class Settings:
    experiment: str
    regions: tuple[str, ...]
    data_bucket: str
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Settings:
        experiment = env.get("FTG_EXPERIMENT", "").strip()
        if not experiment:
            raise SettingsError("FTG_EXPERIMENT is not set")
        regions = tuple(r.strip() for r in env.get("FTG_REGIONS", "").split(",") if r.strip())
        if not regions:
            raise SettingsError("FTG_REGIONS is not set")
        for region in regions:
            if not _REGION_RE.match(region):
                raise SettingsError(f"FTG_REGIONS holds a bad Region name: {region!r}")
        data_bucket = env.get("FTG_DATA_BUCKET", "").strip()
        if not data_bucket:
            raise SettingsError("FTG_DATA_BUCKET is not set")
        raw_age = env.get("FTG_MAX_AGE_SECONDS", "").strip() or str(DEFAULT_MAX_AGE_SECONDS)
        try:
            max_age = int(raw_age)
        except ValueError as exc:
            raise SettingsError("FTG_MAX_AGE_SECONDS must be a whole number of seconds") from exc
        if max_age < 0:
            raise SettingsError("FTG_MAX_AGE_SECONDS must be 0 or more")
        return cls(
            experiment=experiment,
            regions=regions,
            data_bucket=data_bucket,
            max_age_seconds=max_age,
        )
