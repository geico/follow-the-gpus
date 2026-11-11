import pytest

from sweeper.settings import Settings, SettingsError

BASE = {
    "FTG_EXPERIMENT": "ftg-20261015-test",
    "FTG_REGIONS": "us-east-1,us-west-2",
    "FTG_DATA_BUCKET": "ftg-data-0123abcd",
}


def test_each_setting_comes_from_the_environment() -> None:
    assert Settings.from_env({**BASE, "FTG_MAX_AGE_SECONDS": "60"}) == Settings(
        experiment="ftg-20261015-test",
        regions=("us-east-1", "us-west-2"),
        data_bucket="ftg-data-0123abcd",
        max_age_seconds=60,
    )


def test_the_default_max_age_is_180() -> None:
    assert Settings.from_env(BASE).max_age_seconds == 180


@pytest.mark.parametrize("name", ["FTG_EXPERIMENT", "FTG_REGIONS", "FTG_DATA_BUCKET"])
def test_a_missing_required_setting_is_an_error(name: str) -> None:
    env = {k: v for k, v in BASE.items() if k != name}
    with pytest.raises(SettingsError, match=name):
        Settings.from_env(env)


@pytest.mark.parametrize("raw", ["us-east-1,", "us-east-1, us-west-2"])
def test_region_list_tolerates_spaces_and_a_trailing_comma(raw: str) -> None:
    assert Settings.from_env({**BASE, "FTG_REGIONS": raw}).regions[0] == "us-east-1"


@pytest.mark.parametrize("raw", ["US-EAST-1", "us-east", "*"])
def test_a_bad_region_name_is_an_error(raw: str) -> None:
    with pytest.raises(SettingsError, match="FTG_REGIONS"):
        Settings.from_env({**BASE, "FTG_REGIONS": raw})


@pytest.mark.parametrize("raw", ["abc", "1.5", "-1"])
def test_bad_max_age_is_an_error(raw: str) -> None:
    with pytest.raises(SettingsError, match="FTG_MAX_AGE_SECONDS"):
        Settings.from_env({**BASE, "FTG_MAX_AGE_SECONDS": raw})
