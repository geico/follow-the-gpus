"""A failed SDK import fails the run under FTG_REQUIRE_CLOUD_EXTRAS and skips without it."""

import pytest

from tests.conftest import import_or_skip

MISSING = "ftg_no_such_cloud_sdk"


def test_missing_module_skips_without_the_flag(monkeypatch):
    monkeypatch.delenv("FTG_REQUIRE_CLOUD_EXTRAS", raising=False)
    with pytest.raises(pytest.skip.Exception):
        import_or_skip(MISSING)


def test_missing_module_fails_with_the_flag(monkeypatch):
    monkeypatch.setenv("FTG_REQUIRE_CLOUD_EXTRAS", "1")
    with pytest.raises(ModuleNotFoundError):
        import_or_skip(MISSING)


@pytest.mark.parametrize("value", ["0", "false", "no", ""])
def test_a_false_value_does_not_enable_the_flag(monkeypatch, value):
    monkeypatch.setenv("FTG_REQUIRE_CLOUD_EXTRAS", value)
    with pytest.raises(pytest.skip.Exception):
        import_or_skip(MISSING)


@pytest.mark.parametrize("value", ["1", "true", "TRUE"])
def test_one_and_true_enable_the_flag(monkeypatch, value):
    monkeypatch.setenv("FTG_REQUIRE_CLOUD_EXTRAS", value)
    with pytest.raises(ModuleNotFoundError):
        import_or_skip(MISSING)
