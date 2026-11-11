import pytest

from sweeper.settings import Settings, SettingsError


def test_the_default_max_age_applies_when_it_is_not_set() -> None:
    settings = Settings.from_env(
        {"FTG_PROBE_RG": "ftg-probe", "FTG_EXPERIMENT": "ftg-20261012-signals"}
    )
    assert settings == Settings(
        probe_rg="ftg-probe", experiment="ftg-20261012-signals", max_age_seconds=180
    )


def test_missing_experiment_is_an_error() -> None:
    """There is no default experiment name (spec 0007 section 4)."""
    with pytest.raises(SettingsError, match="FTG_EXPERIMENT"):
        Settings.from_env({"FTG_PROBE_RG": "rg"})


def test_each_setting_comes_from_the_environment() -> None:
    settings = Settings.from_env(
        {"FTG_PROBE_RG": "rg", "FTG_EXPERIMENT": "exp2", "FTG_MAX_AGE_SECONDS": "60"}
    )
    assert settings == Settings(probe_rg="rg", experiment="exp2", max_age_seconds=60)


def test_missing_resource_group_is_an_error() -> None:
    with pytest.raises(SettingsError, match="FTG_PROBE_RG"):
        Settings.from_env({})


@pytest.mark.parametrize("raw", ["abc", "1.5", "-1"])
def test_bad_max_age_is_an_error(raw: str) -> None:
    with pytest.raises(SettingsError, match="FTG_MAX_AGE_SECONDS"):
        Settings.from_env({"FTG_PROBE_RG": "rg", "FTG_EXPERIMENT": "e", "FTG_MAX_AGE_SECONDS": raw})
