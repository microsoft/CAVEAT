#!/usr/bin/env python3
"""Bring up one receipt-bound endpoint for TRAIN collection or a dev probe.

This launcher deliberately has no evaluation preparation or worker launch path.  A
caller supplies an exact handoff plus reservation identity, and receives a
create-once endpoint activation suitable for sequential curriculum canaries.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any

WORKSPACE = Path("/home/t-yuxuanli/preference-fidelity")
RESULTS = WORKSPACE / "results/harness_posttrain_campaign2_20260814"
PYTHON = WORKSPACE / ".venv/bin/python"
SUPERVISOR = WORKSPACE / "scripts/supervise_c2_paired_port_forward.py"
HEX = set("0123456789abcdef")


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"handoff is not a JSON object: {path}")
    return value


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_sha(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and not (set(value) - HEX)


def _port_free(port: int) -> bool:
    with socket.socket() as stream:
        return stream.connect_ex(("127.0.0.1", port)) != 0


def _write_new(path: Path, value: Any) -> None:
    payload = json.dumps(value, sort_keys=True, indent=2).encode() + b"\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _pod_matches(pod: str, pod_uid: str) -> bool:
    result = subprocess.run(
        ["kubectl", "-n", "bonete61", "get", "pod", pod, "-o", "json"],
        cwd=WORKSPACE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        return False
    value = json.loads(result.stdout)
    statuses = value.get("status", {}).get("containerStatuses") or []
    return (
        value.get("metadata", {}).get("uid") == pod_uid
        and value.get("status", {}).get("phase") == "Running"
        and len(statuses) == 1
        and statuses[0].get("restartCount") == 0
    )


def _endpoint_ready(port: int, alias: str) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/health", timeout=3
        ) as response:
            if response.status != 200:
                return False
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/v1/models", timeout=5
        ) as response:
            models = json.load(response)
        return alias in {
            row.get("id")
            for row in models.get("data", [])
            if isinstance(row, dict)
        }
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--handoff", type=Path, required=True)
    parser.add_argument("--endpoint-root", type=Path, required=True)
    parser.add_argument("--serve-job", required=True)
    parser.add_argument("--serve-pod", required=True)
    parser.add_argument("--serve-pod-uid", required=True)
    parser.add_argument("--local-port", type=int, required=True)
    parser.add_argument("--handoff-schema", required=True)
    parser.add_argument("--alias-marker", required=True)
    parser.add_argument("--endpoint-timeout-seconds", type=int, default=1200)
    parser.add_argument(
        "--endpoint-role",
        choices=("train_collection", "development_probe"),
        default="train_collection",
    )
    arguments = parser.parse_args(argv)

    handoff_path = arguments.handoff.resolve()
    endpoint_root = arguments.endpoint_root.resolve()
    if RESULTS.resolve() not in endpoint_root.parents:
        parser.error("endpoint root must be a direct campaign-results descendant")
    handoff = _read(handoff_path)
    expected_base = f"http://127.0.0.1:{arguments.local_port}/v1"
    alias = handoff.get("alias")
    role_matches = (
        arguments.endpoint_role == "train_collection"
        or (
            handoff.get("development_probe_only") is True
            and handoff.get("training_eligible") is False
        )
    )
    status_matches = handoff.get("status") == "published_after_successful_receipt"
    identity_matches = _is_sha(handoff.get("candidate_composite_sha256"))
    if arguments.endpoint_role == "development_probe":
        status_matches = status_matches or (
            handoff.get("status") == "published_after_microcheckpoint_inventory"
            and handoff.get("broader_evaluation_authorized") is False
        )
        identity_matches = identity_matches or (
            _is_sha(handoff.get("candidate_tree_sha256"))
            and _is_sha(handoff.get("inventory_file_sha256"))
            and _is_sha(handoff.get("inventory_body_sha256"))
        )
    if (
        handoff.get("schema") != arguments.handoff_schema
        or not status_matches
        or handoff.get("serve_job") != arguments.serve_job
        or handoff.get("serve_pod") != arguments.serve_pod
        or handoff.get("serve_pod_uid") != arguments.serve_pod_uid
        or handoff.get("local_base_url") != expected_base
        or not _is_sha(handoff.get("release_body_sha256"))
        or not identity_matches
        or not isinstance(alias, str)
        or arguments.alias_marker not in alias
        or not _pod_matches(arguments.serve_pod, arguments.serve_pod_uid)
        or not role_matches
    ):
        parser.error("TRAIN endpoint handoff or reserved pod identity changed")
    if endpoint_root.exists():
        parser.error(f"endpoint root already exists: {endpoint_root}")
    if not _port_free(arguments.local_port):
        parser.error(f"local port already in use: {arguments.local_port}")

    endpoint_root.mkdir(parents=True)
    log_path = endpoint_root / "port_forward_supervisor.log"
    stop_file = endpoint_root / "stop_port_forward"
    log = log_path.open("xb")
    supervisor = subprocess.Popen(
        [
            str(PYTHON),
            str(SUPERVISOR),
            "--pod",
            arguments.serve_pod,
            "--pod-uid",
            arguments.serve_pod_uid,
            "--local-port",
            str(arguments.local_port),
            "--stop-file",
            str(stop_file),
        ],
        cwd=WORKSPACE,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log.close()
    deadline = time.monotonic() + arguments.endpoint_timeout_seconds
    while time.monotonic() < deadline:
        if supervisor.poll() is not None:
            raise RuntimeError("TRAIN endpoint tunnel supervisor exited")
        if _endpoint_ready(arguments.local_port, alias):
            break
        time.sleep(5)
    else:
        supervisor.terminate()
        raise TimeoutError("TRAIN endpoint did not become Ready before timeout")

    development_probe = arguments.endpoint_role == "development_probe"
    activation = {
        "schema": (
            "c2-development-probe-candidate-endpoint-activation.v1"
            if development_probe
            else "c2-train-only-candidate-endpoint-activation.v1"
        ),
        "status": (
            "ready_for_development_probe"
            if development_probe
            else "ready_for_train_only_collection"
        ),
        "development_probe_only": development_probe,
        "training_eligible": not development_probe,
        "evaluation_launched": False,
        "handoff": str(handoff_path),
        "handoff_sha256": _sha(handoff_path),
        "alias": alias,
        "release_body_sha256": handoff["release_body_sha256"],
        "candidate_composite_sha256": handoff.get("candidate_composite_sha256")
        or handoff["candidate_tree_sha256"],
        "reservation": {
            "job": arguments.serve_job,
            "pod": arguments.serve_pod,
            "pod_uid": arguments.serve_pod_uid,
        },
        "tunnel": {
            "supervisor_pid": supervisor.pid,
            "local_port": arguments.local_port,
            "base_url": expected_base,
            "health_url": f"http://127.0.0.1:{arguments.local_port}/health",
            "log": str(log_path),
            "stop_file": str(stop_file),
        },
    }
    activation_path = endpoint_root / "activation.json"
    _write_new(activation_path, activation)
    print(json.dumps(activation, sort_keys=True))


if __name__ == "__main__":
    main()
