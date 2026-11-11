"""The ODCR shape-check fixture fails on a broken botocore import under FTG_REQUIRE_CLOUD_EXTRAS."""

import sys

import pytest

from tests.aws import conftest


def _run_fixture(monkeypatch):
    # A None entry in sys.modules makes `import botocore.session` raise ImportError.
    monkeypatch.setitem(sys.modules, "botocore.session", None)
    fixture = conftest.odcr_fakes_match_botocore
    body = getattr(fixture, "_get_wrapped_function", lambda: fixture.__wrapped__)()
    gen = body(monkeypatch)
    next(gen)  # an ImportError that is not swallowed raises here
    gen.close()


def test_a_broken_import_skips_only_the_shape_check_without_the_flag(monkeypatch):
    monkeypatch.delenv("FTG_REQUIRE_CLOUD_EXTRAS", raising=False)
    _run_fixture(monkeypatch)


def test_a_false_flag_still_skips_the_shape_check(monkeypatch):
    monkeypatch.setenv("FTG_REQUIRE_CLOUD_EXTRAS", "0")
    _run_fixture(monkeypatch)


def test_a_broken_import_fails_with_the_flag(monkeypatch):
    monkeypatch.setenv("FTG_REQUIRE_CLOUD_EXTRAS", "1")
    with pytest.raises(ImportError):
        _run_fixture(monkeypatch)
