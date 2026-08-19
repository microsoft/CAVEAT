from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentarena.core import environment
from agentarena.core.environment import Environment


class _Process:
    def __init__(self, returncode=None):
        self.returncode = returncode
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        if self.returncode is None:
            self.returncode = -15

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


class _Environment(Environment):
    name = "testenv"

    def seed_db(self, db_path, *, catalog, condition, params):
        db_path.touch()

    def evaluate(self, handle, task):  # pragma: no cover - not used here
        raise AssertionError("evaluation is outside this lifecycle test")


def _task():
    return SimpleNamespace(
        catalog=None,
        condition="clean",
        params={},
        start_path=None,
    )


def test_default_wait_up_survives_old_forty_second_boundary(monkeypatch):
    clock = [0.0]

    class _Response:
        def read(self):
            return b"{}"

    def urlopen(_url, timeout):
        assert timeout == 5
        if clock[0] < 41.0:
            raise OSError("not ready yet")
        return _Response()

    monkeypatch.setattr(environment.time, "time", lambda: clock[0])
    monkeypatch.setattr(
        environment.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )
    monkeypatch.setattr(environment.urllib.request, "urlopen", urlopen)

    assert environment.wait_up(
        "http://127.0.0.1:1/api/health", process=_Process()
    ) is True
    assert 41.0 <= clock[0] < environment.ENVIRONMENT_STARTUP_TIMEOUT_SECONDS


def test_wait_up_stops_polling_after_early_child_exit(monkeypatch):
    monkeypatch.setattr(
        environment.urllib.request,
        "urlopen",
        lambda *args, **kwargs: pytest.fail("health must not be polled"),
    )
    assert environment.wait_up(
        "http://127.0.0.1:1/api/health", process=_Process(returncode=7)
    ) is False


@pytest.mark.parametrize(
    ("initial_returncode", "marker", "detail"),
    [
        (
            None,
            environment.ENVIRONMENT_STARTUP_TIMEOUT,
            "timeout_seconds=300",
        ),
        (
            7,
            environment.ENVIRONMENT_SERVER_EXITED,
            "returncode=7 before_health",
        ),
    ],
)
def test_start_emits_distinct_timeout_and_early_exit_markers(
    tmp_path, monkeypatch, initial_returncode, marker, detail
):
    process = _Process(returncode=initial_returncode)
    observed = {}

    def popen(*args, **kwargs):
        observed.update(kwargs)
        return process

    def unavailable(_url, *, timeout, process):
        assert timeout == environment.ENVIRONMENT_STARTUP_TIMEOUT_SECONDS
        assert process is not None
        return False

    monkeypatch.setattr(environment, "free_port", lambda _port: None)
    monkeypatch.setattr(environment.subprocess, "Popen", popen)
    monkeypatch.setattr(environment, "wait_up", unavailable)

    with pytest.raises(RuntimeError) as raised:
        _Environment().start(12345, _task(), work_dir=tmp_path)

    assert str(raised.value) == (
        f"{marker}: env=testenv port=12345 {detail}"
    )
    assert observed["stderr"] is environment.subprocess.STDOUT
    server_log = observed["stdout"]
    assert server_log.name == str(tmp_path / "environment_server.log")
    assert server_log.closed is True
    assert (tmp_path / "environment_server.log").is_file()
    assert process.terminated is True


def test_healthy_start_handle_closes_server_log_on_stop(tmp_path, monkeypatch):
    process = _Process()
    freed = []

    monkeypatch.setattr(environment, "free_port", freed.append)
    monkeypatch.setattr(
        environment.subprocess, "Popen", lambda *args, **kwargs: process
    )
    monkeypatch.setattr(
        environment,
        "wait_up",
        lambda _url, *, timeout, process: (
            timeout == environment.ENVIRONMENT_STARTUP_TIMEOUT_SECONDS
            and process is not None
        ),
    )

    handle = _Environment().start(12345, _task(), work_dir=tmp_path)
    assert handle.server_log.closed is False
    assert freed == [12345]

    handle.stop()
    assert process.terminated is True
    assert handle.server_log.closed is True
    assert freed == [12345, 12345]


def test_after_start_failure_cleans_unreturned_handle_and_reraises(
    tmp_path, monkeypatch
):
    process = _Process()
    freed = []
    sentinel = RuntimeError("initial evaluator snapshot failed")
    opened = {}

    def popen(*args, **kwargs):
        opened["server_log"] = kwargs["stdout"]
        return process

    def fail_after_start(_self, _handle, _task):
        raise sentinel

    monkeypatch.setattr(environment, "free_port", freed.append)
    monkeypatch.setattr(environment.subprocess, "Popen", popen)
    monkeypatch.setattr(
        environment,
        "wait_up",
        lambda _url, *, timeout, process: True,
    )
    monkeypatch.setattr(_Environment, "after_start", fail_after_start)

    with pytest.raises(RuntimeError) as raised:
        _Environment().start(12345, _task(), work_dir=tmp_path)

    assert raised.value is sentinel
    assert process.terminated is True
    assert opened["server_log"].closed is True
    assert freed == [12345, 12345]


def test_frozen_runtime_declares_nonbinding_startup_lifecycle():
    from scripts.hard_campaign_runtime import runtime_limit_contract

    configured = runtime_limit_contract()["categories"]["infrastructure"][
        "environment_startup"
    ]["configured"]
    assert configured == {
        "health_total_seconds": 300,
        "health_request_seconds": 5,
        "poll_seconds": 0.4,
        "seed_subprocess_timeout": None,
        "process_early_exit_detection": True,
        "server_output_artifact": "environment_server.log",
        "timeout_marker": "AGENTARENA_ENVIRONMENT_STARTUP_TIMEOUT",
        "early_exit_marker": "AGENTARENA_ENVIRONMENT_SERVER_EXITED",
    }
