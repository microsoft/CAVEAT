#!/usr/bin/env python3
"""Launch two frozen development cases against one LoRA interpolation endpoint."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import socket
import subprocess
import urllib.request
from pathlib import Path
from typing import Any

WORKSPACE = Path("/home/t-yuxuanli/preference-fidelity")
PYTHON = WORKSPACE / ".venv/bin/python"
EVALUATOR_SOURCE = WORKSPACE / "training/harness_posttrain_eval/src"
CAMPAIGN = WORKSPACE / "training/harness_posttrain_eval/configs/campaign.json"
RESULTS = WORKSPACE / "results/harness_posttrain_campaign2_20260814"
SOURCE_ROOT = RESULTS / "step34_rollback_candidate_exact8_r1"
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


def _endpoint_ready(base_url: str, alias: str) -> bool:
    root = base_url.removesuffix("/v1")
    try:
        with urllib.request.urlopen(f"{root}/health", timeout=3) as response:
            if response.status != 200:
                return False
        with urllib.request.urlopen(f"{root}/v1/models", timeout=5) as response:
            models = json.load(response)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return alias in {
        row.get("id") for row in models.get("data", []) if isinstance(row, dict)
    }


def _projection(spec: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(spec)
    value["port"] = "<FRESH_PROBE_PORT>"
    value["out_dir"] = "<FRESH_PROBE_OUTPUT>"
    model = value["model"]
    model["base_url"] = "<CANDIDATE_ENDPOINT>"
    model["deployment"] = "<CANDIDATE_ALIAS>"
    model["name"] = "<CANDIDATE_ALIAS>"
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--alias", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--base-port", type=int, required=True)
    parser.add_argument("--alpha", choices=("1/4", "1/2"), required=True)
    arguments = parser.parse_args()

    output_root = arguments.output_root.resolve()
    if RESULTS.resolve() not in output_root.parents:
        parser.error("output root must be a campaign-results descendant")
    if output_root.exists():
        parser.error(f"probe output root already exists: {output_root}")
    if any(not _port_free(port) for port in range(arguments.base_port, arguments.base_port + 2)):
        parser.error("requested probe ports are not free")
    if not _endpoint_ready(arguments.base_url, arguments.alias):
        parser.error("interpolation endpoint health/alias gate failed")

    source_path = SOURCE_ROOT / "launch_manifest.json"
    source = json.loads(source_path.read_text(encoding="utf-8"))
    by_id = {row["run_id"]: row for row in source["launches"]}
    if any(run_id not in by_id for run_id in SELECTED_RUN_IDS):
        parser.error("frozen exact probe cases are absent")

    launches: list[dict[str, Any]] = []
    invariance: list[dict[str, Any]] = []
    results_root = output_root / "run_results"
    for index, run_id in enumerate(SELECTED_RUN_IDS):
        source_launch = by_id[run_id]
        source_config = Path(source_launch["config"])
        original = json.loads(source_config.read_text(encoding="utf-8"))
        spec = copy.deepcopy(original)
        spec["port"] = arguments.base_port + index
        result = results_root / Path(source_launch["results"]).name
        spec["out_dir"] = str(result)
        spec["model"]["base_url"] = arguments.base_url
        spec["model"]["deployment"] = arguments.alias
        spec["model"]["name"] = arguments.alias
        if _projection(spec) != _projection(original):
            parser.error(f"frozen probe recipe drifted for {run_id}")
        config = output_root / "configs" / f"{index:02d}_{source_config.name}"
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
            "alpha": arguments.alpha,
            "base_port": arguments.base_port,
            "development_probe": True,
            "training_eligible": False,
            "evaluation": output_root.name,
            "endpoint_base_url": arguments.base_url,
            "endpoint_alias": arguments.alias,
            "probe_parent_launch_manifest_sha256": source[
                "launch_manifest_sha256"
            ],
            "probe_cases": list(SELECTED_RUN_IDS),
            "results_root": str(results_root),
            "launches": launches,
        }
    )
    manifest["launch_manifest_sha256"] = _digest(manifest)
    launch_path = output_root / "launch_manifest.json"
    _write_new(launch_path, manifest)
    _write_new(
        output_root / "development_probe_policy.json",
        {
            "schema": "c2-step32-step33-interpolation-development-probe.v1",
            "development_probe": True,
            "training_eligible": False,
            "trajectories_must_not_be_used_for_training": True,
            "alpha": arguments.alpha,
            "source_launch_manifest": str(source_path),
            "source_launch_manifest_file_sha256": _file_digest(source_path),
            "source_launch_manifest_body_sha256": source[
                "launch_manifest_sha256"
            ],
            "probe_launch_manifest": str(launch_path),
            "probe_launch_manifest_body_sha256": manifest[
                "launch_manifest_sha256"
            ],
            "allowed_config_differences": [
                "port",
                "out_dir",
                "model.base_url",
                "model.deployment",
                "model.name",
            ],
            "cases": list(SELECTED_RUN_IDS),
            "invariance": invariance,
        },
    )

    log_path = output_root / "executor_driver.log"
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
            str(output_root / "executor"),
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
                "status": "interpolation_development_probe2_launched",
                "alpha": arguments.alpha,
                "development_probe": True,
                "training_eligible": False,
                "runs": 2,
                "launch_manifest": str(launch_path),
                "launch_manifest_sha256": manifest["launch_manifest_sha256"],
                "pid": process.pid,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
