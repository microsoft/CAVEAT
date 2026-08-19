#!/usr/bin/env python3
"""Launch the two frozen development cases for one Step35 microcheckpoint."""

from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
from pathlib import Path

import launch_c2_interpolation_probe2 as shared


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--alias", required=True)
    parser.add_argument("--profile", choices=("A", "B"), required=True)
    parser.add_argument("--step", type=int, choices=(33, 34, 35), required=True)
    parser.add_argument("--receipt-path", required=True)
    parser.add_argument("--receipt-file-sha256", required=True)
    parser.add_argument(
        "--artifact-kind",
        choices=("training_receipt", "microcheckpoint_inventory"),
        default="training_receipt",
    )
    parser.add_argument("--candidate-tree-sha256", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--base-port", type=int, required=True)
    arguments = parser.parse_args()

    expected_steps = {"A": {33, 34, 35}, "B": {33, 34}}
    if arguments.step not in expected_steps[arguments.profile]:
        parser.error("microcheckpoint is not part of the selected profile")
    for value, label in (
        (arguments.receipt_file_sha256, "receipt SHA-256"),
        (arguments.candidate_tree_sha256, "candidate tree SHA-256"),
    ):
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            parser.error(f"{label} is not a lowercase SHA-256")

    output_root = arguments.output_root.resolve()
    if shared.RESULTS.resolve() not in output_root.parents:
        parser.error("output root must be a campaign-results descendant")
    if output_root.exists():
        parser.error(f"probe output root already exists: {output_root}")
    if any(
        not shared._port_free(port)
        for port in range(arguments.base_port, arguments.base_port + 2)
    ):
        parser.error("requested probe ports are not free")
    if not shared._endpoint_ready(arguments.base_url, arguments.alias):
        parser.error("Step35 endpoint health/alias gate failed")

    source_path = shared.SOURCE_ROOT / "launch_manifest.json"
    source = json.loads(source_path.read_text(encoding="utf-8"))
    by_id = {row["run_id"]: row for row in source["launches"]}
    if any(run_id not in by_id for run_id in shared.SELECTED_RUN_IDS):
        parser.error("frozen exact probe cases are absent")

    launches = []
    invariance = []
    results_root = output_root / "run_results"
    for index, run_id in enumerate(shared.SELECTED_RUN_IDS):
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
        if shared._projection(spec) != shared._projection(original):
            parser.error(f"frozen probe recipe drifted for {run_id}")
        config = output_root / "configs" / f"{index:02d}_{source_config.name}"
        shared._write_new(config, spec)
        launch = copy.deepcopy(source_launch)
        launch.update(
            {
                "argv": [
                    str(shared.PYTHON),
                    "-m",
                    "harness_posttrain_eval.launch_one",
                    "--spec",
                    str(config),
                ],
                "config": str(config),
                "config_sha256": shared._file_digest(config),
                "port": spec["port"],
                "results": spec["out_dir"],
            }
        )
        launches.append(launch)
        invariance.append(
            {
                "run_id": run_id,
                "source_config": str(source_config),
                "source_config_sha256": shared._file_digest(source_config),
                "probe_config": str(config),
                "probe_config_sha256": shared._file_digest(config),
                "frozen_recipe_projection_sha256": shared._digest(
                    shared._projection(spec)
                ),
            }
        )

    manifest = {
        key: copy.deepcopy(value)
        for key, value in source.items()
        if key not in {"launch_manifest_sha256", "launches"}
    }
    manifest.update(
        {
            "base_port": arguments.base_port,
            "candidate_tree_sha256": arguments.candidate_tree_sha256,
            "development_probe": True,
            "training_eligible": False,
            "evaluation": output_root.name,
            "endpoint_base_url": arguments.base_url,
            "endpoint_alias": arguments.alias,
            "profile": arguments.profile,
            "microcheckpoint_step": arguments.step,
            "candidate_artifact_kind": arguments.artifact_kind,
            "candidate_artifact_path": arguments.receipt_path,
            "candidate_artifact_file_sha256": arguments.receipt_file_sha256,
            "probe_parent_launch_manifest_sha256": source[
                "launch_manifest_sha256"
            ],
            "probe_cases": list(shared.SELECTED_RUN_IDS),
            "results_root": str(results_root),
            "launches": launches,
        }
    )
    manifest["launch_manifest_sha256"] = shared._digest(manifest)
    launch_path = output_root / "launch_manifest.json"
    shared._write_new(launch_path, manifest)
    shared._write_new(
        output_root / "development_probe_policy.json",
        {
            "schema": "c2-step35-microcheckpoint-development-probe.v1",
            "development_probe": True,
            "training_eligible": False,
            "trajectories_must_not_be_used_for_training": True,
            "profile": arguments.profile,
            "microcheckpoint_step": arguments.step,
            "candidate_artifact_kind": arguments.artifact_kind,
            "candidate_artifact_path": arguments.receipt_path,
            "candidate_artifact_file_sha256": arguments.receipt_file_sha256,
            "candidate_tree_sha256": arguments.candidate_tree_sha256,
            "source_launch_manifest": str(source_path),
            "source_launch_manifest_file_sha256": shared._file_digest(source_path),
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
            "cases": list(shared.SELECTED_RUN_IDS),
            "invariance": invariance,
        },
    )

    log_path = output_root / "executor_driver.log"
    log = log_path.open("xb")
    environment = dict(os.environ)
    prior = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = str(shared.EVALUATOR_SOURCE) + (
        f":{prior}" if prior else ""
    )
    process = subprocess.Popen(
        [
            str(shared.PYTHON),
            "-m",
            "harness_posttrain_eval.cli",
            "--config",
            str(shared.CAMPAIGN),
            "run-bundle",
            "--launch-manifest",
            str(launch_path),
            "--state-dir",
            str(output_root / "executor"),
            "--working-directory",
            str(shared.WORKSPACE),
            "--jobs",
            "2",
            "--spawn-stagger-seconds",
            "0",
        ],
        cwd=shared.WORKSPACE,
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
                "status": "step35_microcheckpoint_development_probe2_launched",
                "profile": arguments.profile,
                "microcheckpoint_step": arguments.step,
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
