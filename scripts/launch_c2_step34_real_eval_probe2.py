#!/usr/bin/env python3
"""Launch the authorized two-case Step34 development probe.

The probe clones two exact frozen cases byte-for-byte except for isolated ports
and output paths.  It is explicitly development-only and must never feed training.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import socket
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

WORKSPACE = Path("/home/t-yuxuanli/preference-fidelity")
PYTHON = WORKSPACE / ".venv/bin/python"
EVALUATOR_SOURCE = WORKSPACE / "training/harness_posttrain_eval/src"
CAMPAIGN = WORKSPACE / "training/harness_posttrain_eval/configs/campaign.json"
RESULTS = WORKSPACE / "results/harness_posttrain_campaign2_20260814"
SOURCE_ROOT = RESULTS / "step34_rollback_candidate_exact8_r1"
OUTPUT_ROOT = RESULTS / "step34_real_eval_probe2_r1"
BASE_PORT = 52600
SELECTED_RUN_IDS = (
    "grpo_fast::laptop::graded::combined::r00::trained",
    "grpo_fast::laptop::mixed::combined::r01::trained",
)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_new(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, sort_keys=True, indent=2).encode() + b"\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _port_free(port: int) -> bool:
    with socket.socket() as stream:
        return stream.connect_ex(("127.0.0.1", port)) != 0


def _projection(spec: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(spec)
    value["port"] = "<FRESH_PROBE_PORT>"
    value["out_dir"] = "<FRESH_PROBE_OUTPUT>"
    return value


def _running_full_exact() -> int:
    status = json.loads(
        (SOURCE_ROOT / "executor/batch_status.json").read_text(encoding="utf-8")
    )
    runs = status.get("runs") or {}
    if not isinstance(runs, dict):
        raise TypeError("full exact structural status changed")
    return Counter(
        row.get("state") for row in runs.values() if isinstance(row, dict)
    )["running"]


def main() -> None:
    if OUTPUT_ROOT.exists():
        raise SystemExit(f"probe output root already exists: {OUTPUT_ROOT}")
    if any(not _port_free(port) for port in range(BASE_PORT, BASE_PORT + 2)):
        raise SystemExit("probe ports 52600-52601 are not free")
    running = _running_full_exact()
    if running + 2 > 8:
        raise SystemExit(
            f"probe would exceed endpoint concurrency cap: full_exact={running}, probe=2"
        )

    source_path = SOURCE_ROOT / "launch_manifest.json"
    source = json.loads(source_path.read_text(encoding="utf-8"))
    by_id = {row["run_id"]: row for row in source["launches"]}
    if set(by_id) != {
        "grpo_fast::laptop::graded::combined::r00::trained",
        "grpo_fast::laptop::graded::combined::r01::trained",
        "grpo_fast::laptop::graded3::combined::r00::trained",
        "grpo_fast::laptop::graded3::combined::r01::trained",
        "grpo_fast::laptop::graded4::combined::r00::trained",
        "grpo_fast::laptop::graded4::combined::r01::trained",
        "grpo_fast::laptop::mixed::combined::r00::trained",
        "grpo_fast::laptop::mixed::combined::r01::trained",
    }:
        raise SystemExit("frozen exact8 case inventory changed")

    launches: list[dict[str, Any]] = []
    invariance: list[dict[str, Any]] = []
    results_root = OUTPUT_ROOT / "run_results"
    for index, run_id in enumerate(SELECTED_RUN_IDS):
        source_launch = by_id[run_id]
        source_config = Path(source_launch["config"])
        original = json.loads(source_config.read_text(encoding="utf-8"))
        spec = copy.deepcopy(original)
        spec["port"] = BASE_PORT + index
        result = results_root / Path(source_launch["results"]).name
        spec["out_dir"] = str(result)
        if _projection(spec) != _projection(original):
            raise SystemExit(f"probe recipe drifted for {run_id}")
        config = OUTPUT_ROOT / "configs" / f"{index:02d}_{source_config.name}"
        _write_new(config, spec)
        launch = copy.deepcopy(source_launch)
        launch.update(
            {
                "argv": [
                    str(PYTHON),
                    "-m",
                    "harness_posttrain_eval.launch_one",
                    "--spec",
                    str(config),
                ],
                "config": str(config),
                "config_sha256": _file_digest(config),
                "port": spec["port"],
                "results": spec["out_dir"],
            }
        )
        launches.append(launch)
        invariance.append(
            {
                "run_id": run_id,
                "source_config": str(source_config),
                "source_config_sha256": _file_digest(source_config),
                "probe_config": str(config),
                "probe_config_sha256": _file_digest(config),
                "frozen_recipe_projection_sha256": _digest(_projection(spec)),
            }
        )

    manifest = {
        key: copy.deepcopy(value)
        for key, value in source.items()
        if key not in {"launch_manifest_sha256", "launches"}
    }
    manifest.update(
        {
            "base_port": BASE_PORT,
            "development_probe": True,
            "training_eligible": False,
            "evaluation": "step34_real_eval_probe2_r1",
            "probe_parent_launch_manifest_sha256": source["launch_manifest_sha256"],
            "probe_cases": list(SELECTED_RUN_IDS),
            "results_root": str(results_root),
            "launches": launches,
        }
    )
    manifest["launch_manifest_sha256"] = _digest(manifest)
    launch_path = OUTPUT_ROOT / "launch_manifest.json"
    _write_new(launch_path, manifest)
    _write_new(
        OUTPUT_ROOT / "development_probe_policy.json",
        {
            "schema": "c2-step34-real-eval-development-probe.v1",
            "development_probe": True,
            "training_eligible": False,
            "trajectories_must_not_be_used_for_training": True,
            "source_launch_manifest": str(source_path),
            "source_launch_manifest_file_sha256": _file_digest(source_path),
            "source_launch_manifest_body_sha256": source["launch_manifest_sha256"],
            "probe_launch_manifest": str(launch_path),
            "probe_launch_manifest_body_sha256": manifest[
                "launch_manifest_sha256"
            ],
            "allowed_config_differences": ["port", "out_dir"],
            "cases": list(SELECTED_RUN_IDS),
            "invariance": invariance,
        },
    )

    log_path = OUTPUT_ROOT / "executor_driver.log"
    log = log_path.open("xb")
    environment = dict(os.environ)
    prior = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = str(EVALUATOR_SOURCE) + (f":{prior}" if prior else "")
    process = subprocess.Popen(
        [
            str(PYTHON),
            "-m",
            "harness_posttrain_eval.cli",
            "--config",
            str(CAMPAIGN),
            "run-bundle",
            "--launch-manifest",
            str(launch_path),
            "--state-dir",
            str(OUTPUT_ROOT / "executor"),
            "--working-directory",
            str(WORKSPACE),
            "--jobs",
            "2",
            "--spawn-stagger-seconds",
            "0",
        ],
        cwd=WORKSPACE,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log.close()
    print(
        json.dumps(
            {
                "status": "development_probe2_launched",
                "development_probe": True,
                "training_eligible": False,
                "runs": 2,
                "full_exact_running_at_launch": running,
                "total_endpoint_concurrency": running + 2,
                "launch_manifest": str(launch_path),
                "launch_manifest_sha256": manifest["launch_manifest_sha256"],
                "pid": process.pid,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
