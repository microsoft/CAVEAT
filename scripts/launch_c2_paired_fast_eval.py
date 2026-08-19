#!/usr/bin/env python3
"""Start the paired candidate tunnel and concurrent frozen macro4/exact8 evals."""

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
PYTHON = WORKSPACE / ".venv/bin/python"
PREPARE = WORKSPACE / "scripts/prepare_c2_corrective_fast_eval.py"
SUPERVISOR = WORKSPACE / "scripts/supervise_c2_paired_port_forward.py"
CAMPAIGN = WORKSPACE / "training/harness_posttrain_eval/configs/campaign.json"
EVALUATOR_SOURCE = WORKSPACE / "training/harness_posttrain_eval/src"
CAMPAIGN_SHA256 = "c3ed63e45ae86a66186eb92eb0060d0f4c54cd98d2cc24ce446c486129f851ea"
RESULTS = WORKSPACE / "results/harness_posttrain_campaign2_20260814"
ENDPOINT_ROOT = RESULTS / "paired_pref_candidate_endpoint_w1"
MACRO_ROOT = RESULTS / "paired_pref_candidate_macro4_r1"
EXACT_ROOT = RESULTS / "paired_pref_candidate_exact8_r1"
LOCAL_PORT = 18546
MACRO_PORT = 51200
EXACT_PORT = 51300
SERVE_JOB = "t-yuxuanli-hpt-c2-paired-candidate-w1"
SERVE_POD = SERVE_JOB + "-master-0"
SERVE_POD_UID = "ddc10fee-929f-485d-bdce-daae2b884579"
HANDOFF_SCHEMA = "c2-paired-candidate-release-handoff.v1"
ALIAS_MARKER = "paired-buynow"
HEX64 = set("0123456789abcdef")


def read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"not a JSON object: {path}")
    return value


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_sha(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and not (set(value) - HEX64)


def port_free(port: int) -> bool:
    with socket.socket() as stream:
        return stream.connect_ex(("127.0.0.1", port)) != 0


def write_new(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, sort_keys=True, indent=2).encode() + b"\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def pod_matches() -> bool:
    result = subprocess.run(
        ["kubectl", "-n", "bonete61", "get", "pod", SERVE_POD, "-o", "json"],
        cwd=WORKSPACE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        return False
    value = json.loads(result.stdout)
    return (
        value["metadata"]["uid"] == SERVE_POD_UID
        and value["status"].get("phase") not in {"Failed", "Succeeded"}
    )


def endpoint_ready(alias: str) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{LOCAL_PORT}/health", timeout=3
        ) as response:
            if response.status != 200:
                return False
        with urllib.request.urlopen(
            f"http://127.0.0.1:{LOCAL_PORT}/v1/models", timeout=5
        ) as response:
            models = json.load(response)
        return alias in {
            item.get("id") for item in models.get("data", []) if isinstance(item, dict)
        }
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def prepare(mode: str, root: Path, port: int, handoff: dict[str, Any]) -> None:
    subprocess.run(
        [
            str(PYTHON),
            str(PREPARE),
            "--mode",
            mode,
            "--alias",
            handoff["alias"],
            "--base-url",
            handoff["local_base_url"],
            "--endpoint-binding-sha256",
            handoff["release_body_sha256"],
            "--candidate-composite-sha256",
            handoff["candidate_composite_sha256"],
            "--output-root",
            str(root),
            "--base-port",
            str(port),
        ],
        cwd=WORKSPACE,
        check=True,
    )


def launch(root: Path, jobs: int) -> tuple[int, Path]:
    state = root / "executor"
    log_path = root / "executor_driver.log"
    log = log_path.open("xb")
    command = [
        str(PYTHON),
        "-m",
        "harness_posttrain_eval.cli",
        "--config",
        str(CAMPAIGN),
        "run-bundle",
        "--launch-manifest",
        str(root / "launch_manifest.json"),
        "--state-dir",
        str(state),
        "--working-directory",
        str(WORKSPACE),
        "--jobs",
        str(jobs),
        "--spawn-stagger-seconds",
        "0",
    ]
    environment = dict(os.environ)
    prior_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = str(EVALUATOR_SOURCE) + (
        f":{prior_pythonpath}" if prior_pythonpath else ""
    )
    process = subprocess.Popen(
        command,
        cwd=WORKSPACE,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log.close()
    return process.pid, log_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--handoff", type=Path, required=True)
    parser.add_argument("--endpoint-timeout-seconds", type=int, default=1200)
    arguments = parser.parse_args()
    handoff_path = arguments.handoff.resolve()
    handoff = read(handoff_path)
    if (
        handoff.get("schema") != HANDOFF_SCHEMA
        or handoff.get("status") != "published_after_successful_receipt"
        or handoff.get("serve_job") != SERVE_JOB
        or handoff.get("serve_pod") != SERVE_POD
        or handoff.get("serve_pod_uid") != SERVE_POD_UID
        or handoff.get("local_base_url") != f"http://127.0.0.1:{LOCAL_PORT}/v1"
        or not is_sha(handoff.get("release_body_sha256"))
        or not is_sha(handoff.get("candidate_composite_sha256"))
        or not isinstance(handoff.get("alias"), str)
        or ALIAS_MARKER not in handoff["alias"]
        or sha(CAMPAIGN) != CAMPAIGN_SHA256
        or not pod_matches()
    ):
        parser.error("paired release handoff, pod, or fixed campaign config changed")
    for root in (ENDPOINT_ROOT, MACRO_ROOT, EXACT_ROOT):
        if root.exists():
            parser.error(f"activation root already exists: {root}")
    for port in (
        LOCAL_PORT,
        *range(MACRO_PORT, MACRO_PORT + 4),
        *range(EXACT_PORT, EXACT_PORT + 8),
    ):
        if not port_free(port):
            parser.error(f"required local port is already in use: {port}")

    ENDPOINT_ROOT.mkdir(parents=True)
    supervisor_log_path = ENDPOINT_ROOT / "port_forward_supervisor.log"
    supervisor_log = supervisor_log_path.open("xb")
    stop_file = ENDPOINT_ROOT / "stop_port_forward"
    supervisor = subprocess.Popen(
        [
            str(PYTHON),
            str(SUPERVISOR),
            "--pod",
            SERVE_POD,
            "--pod-uid",
            SERVE_POD_UID,
            "--local-port",
            str(LOCAL_PORT),
            "--stop-file",
            str(stop_file),
        ],
        cwd=WORKSPACE,
        stdin=subprocess.DEVNULL,
        stdout=supervisor_log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    supervisor_log.close()

    deadline = time.monotonic() + arguments.endpoint_timeout_seconds
    while time.monotonic() < deadline:
        if supervisor.poll() is not None:
            raise RuntimeError("paired endpoint tunnel supervisor exited")
        if endpoint_ready(handoff["alias"]):
            break
        time.sleep(5)
    else:
        supervisor.terminate()
        raise TimeoutError("paired endpoint did not become ready before timeout")

    prepare("macro4", MACRO_ROOT, MACRO_PORT, handoff)
    prepare("exact8", EXACT_ROOT, EXACT_PORT, handoff)
    macro_pid, macro_log = launch(MACRO_ROOT, 4)
    exact_pid, exact_log = launch(EXACT_ROOT, 8)
    activation = {
        "schema": "c2-paired-fast-eval-activation.v1",
        "status": "macro4_and_exact8_launched_concurrently",
        "handoff": str(handoff_path),
        "handoff_sha256": sha(handoff_path),
        "alias": handoff["alias"],
        "release_body_sha256": handoff["release_body_sha256"],
        "candidate_composite_sha256": handoff["candidate_composite_sha256"],
        "tunnel": {
            "supervisor_pid": supervisor.pid,
            "local_port": LOCAL_PORT,
            "log": str(supervisor_log_path),
            "stop_file": str(stop_file),
        },
        "macro4": {"pid": macro_pid, "root": str(MACRO_ROOT), "log": str(macro_log)},
        "exact8": {"pid": exact_pid, "root": str(EXACT_ROOT), "log": str(exact_log)},
    }
    write_new(ENDPOINT_ROOT / "activation.json", activation)
    print(json.dumps(activation, sort_keys=True))


if __name__ == "__main__":
    main()
