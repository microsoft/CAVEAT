#!/usr/bin/env python3
"""Keep the paired candidate pod's localhost tunnel alive through evaluation."""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


WORKSPACE = Path("/home/t-yuxuanli/preference-fidelity")
NAMESPACE = "bonete61"
STOP = False


def emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, sort_keys=True), flush=True)


def port_free(port: int) -> bool:
    with socket.socket() as stream:
        return stream.connect_ex(("127.0.0.1", port)) != 0


def pod_identity(pod: str) -> tuple[str | None, str]:
    result = subprocess.run(
        ["kubectl", "-n", NAMESPACE, "get", "pod", pod, "-o", "json"],
        cwd=WORKSPACE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        if b"NotFound" in result.stderr:
            return "", "NotFound"
        return None, "Unavailable"
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None, "Unavailable"
    return str(value["metadata"]["uid"]), str(value["status"].get("phase", ""))


def stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def request_stop(_number: int, _frame: object) -> None:
    global STOP
    STOP = True


def main() -> None:
    global STOP
    parser = argparse.ArgumentParser()
    parser.add_argument("--pod", required=True)
    parser.add_argument("--pod-uid", required=True)
    parser.add_argument("--local-port", type=int, required=True)
    parser.add_argument("--remote-port", type=int, default=8000)
    parser.add_argument("--stop-file", type=Path, required=True)
    arguments = parser.parse_args()
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    attempt = 0
    child: subprocess.Popen[bytes] | None = None
    try:
        while not STOP and not arguments.stop_file.exists():
            uid, phase = pod_identity(arguments.pod)
            if uid is None:
                emit({"event": "pod_identity_temporarily_unavailable"})
                time.sleep(1)
                continue
            if uid != arguments.pod_uid or phase in {"Failed", "Succeeded", "NotFound"}:
                emit(
                    {
                        "event": "pod_identity_or_phase_changed",
                        "expected_uid": arguments.pod_uid,
                        "observed_uid": uid,
                        "phase": phase,
                    }
                )
                return
            if not port_free(arguments.local_port):
                emit({"event": "local_port_occupied_without_child"})
                return
            attempt += 1
            emit({"event": "port_forward_start", "attempt": attempt, "phase": phase})
            child = subprocess.Popen(
                [
                    "kubectl",
                    "-n",
                    NAMESPACE,
                    "port-forward",
                    f"pod/{arguments.pod}",
                    f"{arguments.local_port}:{arguments.remote_port}",
                ],
                cwd=WORKSPACE,
                stdin=subprocess.DEVNULL,
                stdout=sys.stdout.buffer,
                stderr=subprocess.STDOUT,
            )
            while (
                child.poll() is None
                and not STOP
                and not arguments.stop_file.exists()
            ):
                time.sleep(5)
                uid, phase = pod_identity(arguments.pod)
                if uid is None:
                    emit({"event": "pod_identity_temporarily_unavailable"})
                    continue
                if uid != arguments.pod_uid or phase in {
                    "Failed",
                    "Succeeded",
                    "NotFound",
                }:
                    emit(
                        {
                            "event": "pod_identity_or_phase_changed",
                            "expected_uid": arguments.pod_uid,
                            "observed_uid": uid,
                            "phase": phase,
                        }
                    )
                    STOP = True
                    break
            stop_process(child)
            code = child.returncode
            child = None
            if not STOP and not arguments.stop_file.exists():
                emit({"event": "port_forward_exit", "returncode": code})
                time.sleep(1)
    finally:
        if child is not None:
            stop_process(child)
        emit({"event": "supervisor_exit", "pid": os.getpid()})


if __name__ == "__main__":
    main()
