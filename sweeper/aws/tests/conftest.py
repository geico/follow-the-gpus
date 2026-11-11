"""No test reaches AWS: each client is a real boto3 client behind a botocore Stubber."""

import pytest


@pytest.fixture(autouse=True)
def _no_real_credentials(monkeypatch):
    # The Stubber answers before a request is signed or sent; these values only keep botocore
    # from searching the machine for a real profile.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", "/dev/null")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "/dev/null")
