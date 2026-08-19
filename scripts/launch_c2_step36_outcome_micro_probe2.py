#!/usr/bin/env python3
"""Launch the two frozen development cases for one Step36 microcheckpoint."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import c2_step36_outcome_candidate_contract as contract


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--launch", action="store_true", required=True)
    parser.add_argument("--stage", type=int, choices=contract.STEPS, required=True)
    parser.add_argument("--step", type=int, choices=contract.STEPS, required=True)
    parser.add_argument("--handoff", type=Path)
    arguments = parser.parse_args()
    stage = arguments.stage
    step = arguments.step
    if step not in contract.served_steps(stage):
        parser.error("probe step is not exposed by this sealed server generation")
    handoff_path = (arguments.handoff or contract.HANDOFF_BY_STAGE[stage]).resolve()
    if handoff_path != contract.HANDOFF_BY_STAGE[stage]:
        parser.error("handoff is not the planned Step36 generation path")
    if (
        contract.file_sha256(contract.DEV_PROBE_LAUNCHER)
        != contract.DEV_PROBE_LAUNCHER_SHA256
    ):
        parser.error("proven Step35 development-probe2 launcher changed")
    if handoff_path.is_symlink() or not handoff_path.is_file():
        parser.error("published Step36 handoff is absent or unsafe")
    value = json.loads(handoff_path.read_text(encoding="utf-8"))
    validated = value.get("validated_profile") or {}
    adapters = validated.get("adapters") or []
    adapter = next((item for item in adapters if item.get("step") == step), None)
    artifacts = value.get("candidate_artifacts") or []
    if value.get("stage") == 35:
        artifact = artifacts[0] if len(artifacts) == 1 else None
    else:
        artifact = next((item for item in artifacts if item.get("step") == step), None)
    expected_url = f"http://127.0.0.1:{contract.LOCAL_PORT_BY_STAGE[stage]}/v1"
    if (
        value.get("schema") != contract.HANDOFF_SCHEMA
        or value.get("stage") != stage
        or value.get("steps") != list(contract.served_steps(stage))
        or value.get("development_probe_only") is not True
        or value.get("training_eligible") is not False
        or value.get("full_evaluation_authorized") is not False
        or value.get("local_base_url") != expected_url
        or not isinstance(adapter, dict)
        or not isinstance(artifact, dict)
        or adapter.get("alias")
        != contract.exact_alias(step, str(adapter.get("tree_sha256", "")))
    ):
        parser.error("Step36 handoff/probe binding changed")
    kind = artifact.get("kind")
    if kind not in {"microcheckpoint_inventory", "training_receipt"}:
        parser.error("Step36 candidate artifact kind changed")
    for key in ("file_sha256", "body_sha256"):
        contract.require_sha256(artifact.get(key), f"candidate artifact {key}")

    python = contract.WORKSPACE / ".venv/bin/python"
    argv = [
        str(python),
        str(contract.DEV_PROBE_LAUNCHER),
        "--base-url",
        expected_url,
        "--alias",
        adapter["alias"],
        "--profile",
        contract.PROFILE,
        "--step",
        str(step),
        "--receipt-path",
        artifact["path"],
        "--receipt-file-sha256",
        artifact["file_sha256"],
        "--candidate-tree-sha256",
        adapter["tree_sha256"],
        "--output-root",
        str(contract.PROBE_OUTPUT_ROOT_BY_STEP[step]),
        "--base-port",
        str(contract.PROBE_BASE_PORT_BY_STEP[step]),
        "--artifact-kind",
        kind,
    ]
    os.execv(str(python), argv)


if __name__ == "__main__":
    main()
