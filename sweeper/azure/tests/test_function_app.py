"""Checks on the Function App shell: the trigger and the package pins. No host, no network."""

import logging
import tomllib
from pathlib import Path

import pytest

from sweeper.settings import Settings

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _reset_log_handler_cache():
    """`function_app._log_handler` is a module-level cache set at cold start (section 9), and
    `attach_blob_log_handler` adds a handler to the root logger, a global singleton."""
    import function_app

    saved_cache = function_app._log_handler
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    function_app._log_handler = None
    yield
    function_app._log_handler = saved_cache
    root.handlers[:] = saved_handlers


def test_timer_runs_every_minute_and_not_on_startup() -> None:
    import function_app

    (function,) = function_app.app.get_functions()
    (binding,) = function.get_bindings()
    assert binding.get_dict_repr()["type"] == "timerTrigger"
    assert binding.get_dict_repr()["schedule"] == "0 */1 * * * *"
    assert binding.get_dict_repr()["runOnStartup"] is False


def test_requirements_match_the_pyproject_pins() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    pins = set(pyproject["project"]["dependencies"])
    lines = (ROOT / "requirements.txt").read_text().splitlines()
    requirements = {line.strip() for line in lines if line.strip() and not line.startswith("#")}
    assert requirements == pins
    assert all("==" in pin for pin in pins)


def test_zip_root_has_the_files_that_the_functions_host_needs() -> None:
    for name in ("function_app.py", "host.json", "requirements.txt", "sweeper/__init__.py"):
        assert (ROOT / name).is_file(), name


def test_sweeper_logger_sends_its_records_to_the_root_logger() -> None:
    import function_app

    assert function_app.log.name == "sweeper"
    assert function_app.log.propagate is True
    assert function_app.log.handlers == []


def test_no_log_handler_when_no_data_storage_account_is_set() -> None:
    import function_app

    settings = Settings(probe_rg="rg", experiment="ftg")
    before = logging.getLogger().handlers[:]
    assert function_app._attach_log_handler_once(settings) is None
    assert logging.getLogger().handlers == before
    # The decision is cached at cold start: a later call with an account set still does nothing.
    settings_with_account = Settings(probe_rg="rg", experiment="ftg", data_storage_account="acct")
    assert function_app._attach_log_handler_once(settings_with_account) is None


def test_log_handler_attaches_once_when_a_data_storage_account_is_set(monkeypatch) -> None:
    import function_app

    class FakeContainerClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def upload_blob(self, **kwargs):
            pass

    monkeypatch.setattr("azure.storage.blob.ContainerClient", FakeContainerClient)
    monkeypatch.setattr(
        "azure.identity.DefaultAzureCredential", lambda: "fake-credential", raising=False
    )
    settings = Settings(probe_rg="rg", experiment="ftg", data_storage_account="myaccount")
    handler = function_app._attach_log_handler_once(settings)
    assert handler is not None
    assert handler in logging.getLogger().handlers
    assert handler.container.kwargs["account_url"] == "https://myaccount.blob.core.windows.net"
    assert handler.container.kwargs["container_name"] == "data"
    # A second call, even with different settings, reuses the cached handler (cold start only).
    again = function_app._attach_log_handler_once(settings)
    assert again is handler


def test_a_failing_store_client_does_not_stop_the_sweep(monkeypatch, capsys) -> None:
    import function_app

    class FailingContainerClient:
        def __init__(self, **kwargs):
            raise RuntimeError("no store")

    monkeypatch.setattr("azure.storage.blob.ContainerClient", FailingContainerClient)
    monkeypatch.setattr(
        "azure.identity.DefaultAzureCredential", lambda: "fake-credential", raising=False
    )
    ran = []
    monkeypatch.setattr(function_app, "build_clients", lambda env: (None, None, None))
    monkeypatch.setattr(function_app, "sweep", lambda *a, **k: ran.append(True))
    monkeypatch.setenv("FTG_PROBE_RG", "rg")
    monkeypatch.setenv("FTG_EXPERIMENT", "ftg-20261014-test")
    monkeypatch.setenv("FTG_DATA_STORAGE_ACCOUNT", "acct")

    class Timer:
        past_due = False

    function_app.sweep_probe_resources._function._func(Timer())
    assert ran == [True]
    assert function_app._log_handler is False
    assert "store log handler not attached" in capsys.readouterr().out


def _run_timer_with_store(monkeypatch):
    import function_app

    class FakeContainerClient:
        def __init__(self, **kwargs):
            pass

        def upload_blob(self, **kwargs):
            pass

    monkeypatch.setattr("azure.storage.blob.ContainerClient", FakeContainerClient)
    monkeypatch.setattr(
        "azure.identity.DefaultAzureCredential", lambda: "fake-credential", raising=False
    )
    monkeypatch.setattr(function_app, "build_clients", lambda env: (None, None, None))
    monkeypatch.setattr(function_app, "sweep", lambda *a, **k: None)
    monkeypatch.setenv("FTG_PROBE_RG", "rg")
    monkeypatch.setenv("FTG_EXPERIMENT", "ftg-20261014-test")
    monkeypatch.setenv("FTG_DATA_STORAGE_ACCOUNT", "acct")

    class Timer:
        past_due = False

    function_app.sweep_probe_resources._function._func(Timer())


def _blob_handlers():
    from sweeper.log_store import BlobLogHandler

    return [h for h in logging.getLogger().handlers if isinstance(h, BlobLogHandler)]


def test_a_warm_host_keeps_one_store_log_handler(monkeypatch) -> None:
    _run_timer_with_store(monkeypatch)
    _run_timer_with_store(monkeypatch)
    assert len(_blob_handlers()) == 1


def test_the_attached_store_log_handler_has_no_filter(monkeypatch) -> None:
    _run_timer_with_store(monkeypatch)
    (attached,) = _blob_handlers()
    assert attached.filters == []


def test_a_failed_client_build_is_logged_and_flushed(monkeypatch, caplog) -> None:
    import function_app

    flushed = []

    class Handler:
        def flush_to_store(self):
            flushed.append(True)

    def no_subscription(env):
        raise ValueError("AZURE_SUBSCRIPTION_ID is not set")

    monkeypatch.setattr(function_app, "_attach_log_handler_once", lambda settings: Handler())
    monkeypatch.setattr(function_app, "build_clients", no_subscription)
    monkeypatch.setenv("FTG_PROBE_RG", "rg")
    monkeypatch.setenv("FTG_EXPERIMENT", "ftg-20261014-test")

    class Timer:
        past_due = False

    with caplog.at_level(logging.ERROR, logger="sweeper"):
        with pytest.raises(ValueError, match="AZURE_SUBSCRIPTION_ID"):
            function_app.sweep_probe_resources._function._func(Timer())
    assert flushed == [True]
    assert "AZURE_SUBSCRIPTION_ID" in caplog.text


def test_code_sha_matches_the_algorithm_that_opentofu_uses(tmp_path: Path) -> None:
    """Same files, same entry format as `local.sweeper_src_sha` in terraform/azure/sweeper.tf."""
    import hashlib

    from sweeper.code_sha import code_sha

    (tmp_path / "sweeper").mkdir()
    (tmp_path / "sweeper" / "__pycache__").mkdir()
    (tmp_path / "tests").mkdir()
    files = {
        "function_app.py": b"app",
        "host.json": b"{}",
        "requirements.txt": b"a==1",
        "sweeper/__init__.py": b"",
        "sweeper/run.py": b"run",
    }
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
    # Left out of the hash: tests, caches, and files the zip carries but the runtime ignores.
    (tmp_path / "tests" / "test_x.py").write_bytes(b"t")
    (tmp_path / "sweeper" / "__pycache__" / "x.py").write_bytes(b"c")
    (tmp_path / "pyproject.toml").write_bytes(b"p")
    entries = ",".join(f"{n}:{hashlib.sha256(d).hexdigest()}" for n, d in sorted(files.items()))
    assert code_sha(tmp_path) == hashlib.sha256(entries.encode()).hexdigest()


def test_each_invocation_logs_the_code_sha(caplog: pytest.LogCaptureFixture, monkeypatch) -> None:
    import function_app

    def stop(_env):
        raise RuntimeError("stop after the first log line")

    monkeypatch.setenv("FTG_PROBE_RG", "rg")
    monkeypatch.setenv("FTG_EXPERIMENT", "exp")
    monkeypatch.setattr(function_app, "build_clients", stop)
    timer = type("T", (), {"past_due": False})()
    with caplog.at_level(logging.INFO, logger="sweeper"), pytest.raises(RuntimeError):
        function_app.sweep_probe_resources.build().get_user_function()(timer)
    assert any(f"code_sha={function_app._code_sha()}" in r.getMessage() for r in caplog.records)
