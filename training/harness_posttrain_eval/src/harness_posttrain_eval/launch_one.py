from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
from pathlib import Path

from .common import read_json


def _stop_process_group(process: subprocess.Popen[bytes], *, grace_seconds: float = 30) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, required=True)
    arguments = parser.parse_args(argv)
    spec = read_json(arguments.spec.resolve())
    timeout = int(spec["run_timeout_seconds"])
    environment = os.environ.copy()
    runtime_environment = spec.get("runtime_environment")
    if not isinstance(runtime_environment, dict) or not runtime_environment:
        parser.error("spec.runtime_environment must be a nonempty object")
    for key, value in runtime_environment.items():
        if not isinstance(key, str) or not isinstance(value, str):
            parser.error("spec.runtime_environment must contain only string pairs")
        environment[key] = value
    process = subprocess.Popen(
        [sys.executable, "-m", "agentarena.run_cell"],
        stdin=subprocess.PIPE,
        env=environment,
        start_new_session=True,
    )
    previous_handlers: dict[signal.Signals, signal.Handlers] = {}

    def forward(signum: int, _frame: object) -> None:
        _stop_process_group(process)
        raise SystemExit(128 + signum)

    for signum in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[signum] = signal.signal(signum, forward)
    try:
        process.communicate(input=arguments.spec.read_bytes(), timeout=timeout)
    except subprocess.TimeoutExpired:
        _stop_process_group(process)
        print(f"safety backstop bound for {spec['run_id']}", file=sys.stderr)
        return 124
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    return int(process.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
