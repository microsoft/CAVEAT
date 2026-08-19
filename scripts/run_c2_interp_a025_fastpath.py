#!/usr/bin/env python3
"""Wait for alpha.25 capacity, publish, serve, and launch its two dev cases."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import launch_c2_train_endpoint as endpoint

WORKSPACE = Path("/home/t-yuxuanli/preference-fidelity")
RESULTS = WORKSPACE / "results/harness_posttrain_campaign2_20260814"
JOB = "t-yuxuanli-hpt-c2-interp-a025-candidate-w3"
POD = JOB + "-master-0"
POD_UID = "fb1adbb0-0659-445e-b582-7f38e8f373b3"
HANDOFF = RESULTS / "orchestration/c2_interp_a025_candidate_release_handoff_w3.json"
ENDPOINT_ROOT = RESULTS / "step32_step33_interp_a025_endpoint_w3"
PROBE_ROOT = RESULTS / "step32_step33_interp_a025_probe2_w3"


def _pod() -> dict[str, object] | None:
    result = subprocess.run(
        ["kubectl", "-n", "bonete61", "get", "pod", POD, "-o", "json"],
        cwd=WORKSPACE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        return None
    return json.loads(result.stdout)


def main() -> None:
    if any(path.exists() for path in (HANDOFF, ENDPOINT_ROOT, PROBE_ROOT)):
        raise RuntimeError("alpha.25 fastpath output already exists")
    deadline = time.monotonic() + 4 * 60 * 60
    while time.monotonic() < deadline:
        value = _pod()
        if value is not None:
            statuses = value.get("status", {}).get("containerStatuses") or []
            if value.get("metadata", {}).get("uid") != POD_UID:
                raise RuntimeError("alpha.25 reserved pod UID changed")
            if (
                value.get("status", {}).get("phase") == "Running"
                and len(statuses) == 1
                and statuses[0].get("restartCount") == 0
            ):
                break
        time.sleep(10)
    else:
        raise TimeoutError("alpha.25 reservation did not schedule within four hours")
    print(json.dumps({"milestone": "reservation_running", "pod_uid": POD_UID}), flush=True)

    subprocess.run(
        [
            "python3",
            str(WORKSPACE / "scripts/release_c2_interp_a025_candidate.py"),
            "--publish",
            "--handoff-output",
            str(HANDOFF),
        ],
        cwd=WORKSPACE,
        check=True,
    )
    handoff = json.loads(HANDOFF.read_text(encoding="utf-8"))
    alias = handoff["alias"]
    endpoint.main(
        [
            "--handoff",
            str(HANDOFF),
            "--endpoint-root",
            str(ENDPOINT_ROOT),
            "--serve-job",
            JOB,
            "--serve-pod",
            POD,
            "--serve-pod-uid",
            POD_UID,
            "--local-port",
            "18554",
            "--handoff-schema",
            "c2-step32-step33-interp-a025-candidate-handoff.v1",
            "--alias-marker",
            "step32-step33-interp-a025",
            "--endpoint-role",
            "development_probe",
        ]
    )
    print(json.dumps({"milestone": "endpoint_ready", "alias": alias}), flush=True)
    subprocess.run(
        [
            "python3",
            str(WORKSPACE / "scripts/launch_c2_interpolation_probe2.py"),
            "--base-url",
            "http://127.0.0.1:18554/v1",
            "--alias",
            alias,
            "--output-root",
            str(PROBE_ROOT),
            "--base-port",
            "52610",
            "--alpha",
            "1/4",
        ],
        cwd=WORKSPACE,
        check=True,
    )
    print(json.dumps({"milestone": "probe2_running", "root": str(PROBE_ROOT)}), flush=True)


if __name__ == "__main__":
    main()
