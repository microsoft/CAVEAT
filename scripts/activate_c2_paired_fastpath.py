#!/usr/bin/env python3
"""Wait for complete paired receipt JSON, then publish, serve, and launch evals."""

from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import time
from pathlib import Path


WORKSPACE = Path("/home/t-yuxuanli/preference-fidelity")
PYTHON = WORKSPACE / ".venv/bin/python"
PUBLISHER = WORKSPACE / "scripts/release_c2_paired_candidate.py"
LAUNCHER = WORKSPACE / "scripts/launch_c2_paired_fast_eval.py"
HANDOFF = (
    WORKSPACE
    / "results/harness_posttrain_campaign2_20260814/orchestration/"
    "c2_paired_candidate_release_handoff_w1.json"
)
NAMESPACE = "bonete61"
POD = "t-yuxuanli-hpt-c2-paired-candidate-w1-master-0"
RECEIPT = Path(
    "/data/runs/t-yuxuanli/t-yuxuanli-hpt-c2-paired-pref-w1-20260815/"
    "training_r1/training/training_receipt.json"
)
PROBE = r"""
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
if not path.is_file() or path.is_symlink():
    raise SystemExit(1)
try:
    value = json.loads(path.read_text(encoding="utf-8"))
except (OSError, UnicodeDecodeError, json.JSONDecodeError):
    raise SystemExit(1)
if (
    not isinstance(value, dict)
    or value.get("status") != "ok"
    or not isinstance(value.get("receipt_body_sha256"), str)
    or len(value["receipt_body_sha256"]) != 64
    or not isinstance(value.get("candidate"), dict)
):
    raise SystemExit(1)
print(json.dumps({"complete_json": True, "receipt_body_sha256": value["receipt_body_sha256"]}))
"""


def now() -> str:
    return datetime.datetime.now(datetime.UTC).replace(microsecond=0).isoformat()


def probe_receipt() -> dict[str, object] | None:
    result = subprocess.run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "exec",
            POD,
            "--",
            "python3",
            "-c",
            PROBE,
            str(RECEIPT),
        ],
        cwd=WORKSPACE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        return None
    value = json.loads(result.stdout)
    return value if isinstance(value, dict) else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt-timeout-seconds", type=int, default=7200)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--endpoint-timeout-seconds", type=int, default=1200)
    arguments = parser.parse_args()
    if HANDOFF.exists():
        parser.error(f"handoff already exists: {HANDOFF}")

    deadline = time.monotonic() + arguments.receipt_timeout_seconds
    while time.monotonic() < deadline:
        receipt = probe_receipt()
        if receipt is not None:
            print(
                json.dumps({"at": now(), "event": "receipt_complete_json", **receipt}),
                flush=True,
            )
            break
        time.sleep(arguments.poll_seconds)
    else:
        raise TimeoutError("paired receipt did not become complete JSON before timeout")

    subprocess.run(
        [
            str(PYTHON),
            str(PUBLISHER),
            "--publish",
            "--handoff-output",
            str(HANDOFF),
        ],
        cwd=WORKSPACE,
        check=True,
    )
    print(json.dumps({"at": now(), "event": "release_published"}), flush=True)
    subprocess.run(
        [
            str(PYTHON),
            str(LAUNCHER),
            "--handoff",
            str(HANDOFF),
            "--endpoint-timeout-seconds",
            str(arguments.endpoint_timeout_seconds),
        ],
        cwd=WORKSPACE,
        check=True,
    )
    print(json.dumps({"at": now(), "event": "macro4_exact8_launched"}), flush=True)


if __name__ == "__main__":
    main()
