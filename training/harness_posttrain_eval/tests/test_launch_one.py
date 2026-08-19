from __future__ import annotations

import json
import subprocess

from harness_posttrain_eval import launch_one


def test_timeout_terminates_the_entire_run_cell_process_group(tmp_path, monkeypatch):
    spec = tmp_path / "run.json"
    spec.write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "run_timeout_seconds": 10,
                "runtime_environment": {"CAMPAIGN_TEST": "exact"},
            }
        ),
        encoding="utf-8",
    )
    killed = []

    class FakeProcess:
        pid = 4242
        returncode = -15

        def __init__(self, argv, **kwargs):
            assert argv[-2:] == ["-m", "agentarena.run_cell"]
            assert kwargs["stdin"] is subprocess.PIPE
            assert kwargs["start_new_session"] is True
            assert kwargs["env"]["CAMPAIGN_TEST"] == "exact"

        def communicate(self, *, input, timeout):
            assert json.loads(input)["run_id"] == "run-1"
            assert timeout == 10
            raise subprocess.TimeoutExpired("agentarena.run_cell", timeout)

        def poll(self):
            return None

        def wait(self, timeout=None):
            assert timeout == 30
            return self.returncode

    monkeypatch.setattr(launch_one.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(
        launch_one.os, "killpg", lambda pid, signum: killed.append((pid, signum))
    )

    assert launch_one.main(["--spec", str(spec)]) == 124
    assert killed == [(4242, launch_one.signal.SIGTERM)]
