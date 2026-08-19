#!/usr/bin/env python3
"""Activate one published Step36 candidate generation for development probes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import c2_step36_outcome_candidate_contract as contract
import launch_c2_train_endpoint as shared


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--launch", action="store_true", required=True)
    parser.add_argument("--stage", type=int, choices=contract.STEPS, required=True)
    parser.add_argument("--pod-uid", required=True)
    parser.add_argument("--handoff", type=Path)
    arguments = parser.parse_args()
    stage = arguments.stage
    handoff = (arguments.handoff or contract.HANDOFF_BY_STAGE[stage]).resolve()
    if handoff != contract.HANDOFF_BY_STAGE[stage]:
        parser.error("handoff is not the planned Step36 generation path")
    if contract.file_sha256(contract.ENDPOINT_LAUNCHER) != contract.ENDPOINT_LAUNCHER_SHA256:
        parser.error("proven development endpoint launcher changed")
    if not handoff.is_file() or handoff.is_symlink():
        parser.error("published Step36 handoff is absent or unsafe")
    value = json.loads(handoff.read_text(encoding="utf-8"))
    if (
        value.get("schema") != contract.HANDOFF_SCHEMA
        or value.get("stage") != stage
        or value.get("development_probe_only") is not True
        or value.get("training_eligible") is not False
        or value.get("full_evaluation_authorized") is not False
    ):
        parser.error("Step36 development handoff policy changed")
    shared.main(
        [
            "--handoff",
            str(handoff),
            "--endpoint-root",
            str(contract.ENDPOINT_ROOT_BY_STAGE[stage]),
            "--serve-job",
            contract.JOB_BY_STAGE[stage],
            "--serve-pod",
            contract.POD_BY_STAGE[stage],
            "--serve-pod-uid",
            arguments.pod_uid,
            "--local-port",
            str(contract.LOCAL_PORT_BY_STAGE[stage]),
            "--handoff-schema",
            contract.HANDOFF_SCHEMA,
            "--alias-marker",
            "step35-trajectory-proximal-a-step",
            "--endpoint-role",
            "development_probe",
        ]
    )


if __name__ == "__main__":
    main()
