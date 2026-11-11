"""The settings of the default boto3 client. Fake `boto3` and `botocore` modules: no network."""

import sys
import types

from ftg_prober.probes import Clients


def install_fakes(monkeypatch):
    made = []
    boto3 = types.ModuleType("boto3")

    class Session:  # a client comes from its own Session, not the default one
        def client(self, service, **kwargs):
            made.append((service, kwargs))
            return object()

    session = types.ModuleType("boto3.session")
    session.Session = Session
    boto3.session = session
    botocore = types.ModuleType("botocore")
    config = types.ModuleType("botocore.config")

    class Config:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    config.Config = Config
    botocore.config = config
    modules = (
        ("boto3", boto3),
        ("boto3.session", session),
        ("botocore", botocore),
        ("botocore.config", config),
    )
    for name, module in modules:
        monkeypatch.setitem(sys.modules, name, module)
    return made


def test_a_client_has_timeouts_and_the_standard_retry_mode_with_3_attempts(monkeypatch):
    made = install_fakes(monkeypatch)
    Clients().boto3("ec2", "us-east-1")

    service, kwargs = made[0]
    assert service == "ec2"
    assert kwargs["region_name"] == "us-east-1"
    assert kwargs["config"].kwargs == {
        "connect_timeout": 5,
        "read_timeout": 30,
        "retries": {"mode": "standard", "max_attempts": 3},
    }


def test_the_capacity_block_client_has_1_attempt_and_is_a_separate_client(monkeypatch):
    made = install_fakes(monkeypatch)
    clients = Clients()
    spot = clients.boto3("ec2", "us-east-1")
    blocks = clients.boto3("ec2", "us-east-1", max_attempts=1)

    assert spot is not blocks
    assert clients.boto3("ec2", "us-east-1", max_attempts=1) is blocks  # cached
    assert made[1][1]["config"].kwargs["retries"] == {"mode": "standard", "max_attempts": 1}
    assert made[1][1]["config"].kwargs["connect_timeout"] == 5


def test_the_s3_store_client_has_the_same_timeouts_and_retries(monkeypatch):
    from ftg_prober.store import S3Store

    made = install_fakes(monkeypatch)
    assert S3Store("bucket").client is not None

    service, kwargs = made[0]
    assert service == "s3"
    assert kwargs["config"].kwargs == {
        "connect_timeout": 5,
        "read_timeout": 30,
        "retries": {"mode": "standard", "max_attempts": 3},
    }
