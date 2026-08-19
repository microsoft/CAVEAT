#!/usr/bin/env python3
"""Create the activation record after a launcher-ready write race/failure.

This never starts a tunnel or server. It only accepts an already-running exact
UID-bound supervisor and repeats the same pod, handoff, health, and alias gates
as the endpoint launcher before writing the missing activation create-once.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import launch_c2_train_endpoint as shared


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--handoff", type=Path, required=True)
    parser.add_argument("--endpoint-root", type=Path, required=True)
    parser.add_argument("--serve-job", required=True)
    parser.add_argument("--serve-pod", required=True)
    parser.add_argument("--serve-pod-uid", required=True)
    parser.add_argument("--local-port", type=int, required=True)
    parser.add_argument("--handoff-schema", required=True)
    parser.add_argument("--alias-marker", required=True)
    parser.add_argument("--supervisor-pid", type=int, required=True)
    arguments = parser.parse_args()

    endpoint_root = arguments.endpoint_root.resolve()
    handoff_path = arguments.handoff.resolve()
    handoff = shared._read(handoff_path)
    alias = handoff.get("alias")
    activation_path = endpoint_root / "activation.json"
    log_path = endpoint_root / "port_forward_supervisor.log"
    stop_file = endpoint_root / "stop_port_forward"

    try:
        os.kill(arguments.supervisor_pid, 0)
    except OSError as error:
        parser.error(f"tunnel supervisor is not alive: {error}")
    pre_final = handoff.get("status") == "published_after_microcheckpoint_inventory"
    final_receipt = handoff.get("status") == "published_after_successful_receipt"
    if (
        shared.RESULTS.resolve() not in endpoint_root.parents
        or not endpoint_root.is_dir()
        or activation_path.exists()
        or not log_path.is_file()
        or handoff.get("schema") != arguments.handoff_schema
        or not (pre_final or final_receipt)
        or handoff.get("development_probe_only") is not True
        or handoff.get("training_eligible") is not False
        or (
            pre_final
            and handoff.get("broader_evaluation_authorized") is not False
        )
        or handoff.get("serve_job") != arguments.serve_job
        or handoff.get("serve_pod") != arguments.serve_pod
        or handoff.get("serve_pod_uid") != arguments.serve_pod_uid
        or handoff.get("local_base_url")
        != f"http://127.0.0.1:{arguments.local_port}/v1"
        or not shared._is_sha(handoff.get("release_body_sha256"))
        or (
            pre_final
            and (
                not shared._is_sha(handoff.get("candidate_tree_sha256"))
                or not shared._is_sha(handoff.get("inventory_file_sha256"))
                or not shared._is_sha(handoff.get("inventory_body_sha256"))
            )
        )
        or (
            final_receipt
            and (
                not shared._is_sha(handoff.get("candidate_composite_sha256"))
                or not shared._is_sha(handoff.get("receipt_file_sha256"))
                or not shared._is_sha(handoff.get("receipt_body_sha256"))
            )
        )
        or not isinstance(alias, str)
        or arguments.alias_marker not in alias
        or not shared._pod_matches(arguments.serve_pod, arguments.serve_pod_uid)
        or not shared._endpoint_ready(arguments.local_port, alias)
    ):
        parser.error("existing development endpoint identity/readiness changed")

    candidate_identity = (
        handoff["candidate_tree_sha256"]
        if pre_final
        else handoff["candidate_composite_sha256"]
    )
    artifact_kind = (
        "microcheckpoint_inventory" if pre_final else "training_receipt"
    )
    artifact_file = (
        handoff["inventory_file_sha256"]
        if pre_final
        else handoff["receipt_file_sha256"]
    )
    artifact_body = (
        handoff["inventory_body_sha256"]
        if pre_final
        else handoff["receipt_body_sha256"]
    )
    activation = {
        "schema": "c2-development-probe-candidate-endpoint-activation.v1",
        "status": "ready_for_development_probe",
        "development_probe_only": True,
        "training_eligible": False,
        "evaluation_launched": False,
        "handoff": str(handoff_path),
        "handoff_sha256": shared._sha(handoff_path),
        "alias": alias,
        "release_body_sha256": handoff["release_body_sha256"],
        "candidate_composite_sha256": candidate_identity,
        "candidate_artifact_kind": artifact_kind,
        "candidate_artifact_file_sha256": artifact_file,
        "candidate_artifact_body_sha256": artifact_body,
        "reservation": {
            "job": arguments.serve_job,
            "pod": arguments.serve_pod,
            "pod_uid": arguments.serve_pod_uid,
        },
        "tunnel": {
            "supervisor_pid": arguments.supervisor_pid,
            "local_port": arguments.local_port,
            "base_url": f"http://127.0.0.1:{arguments.local_port}/v1",
            "health_url": f"http://127.0.0.1:{arguments.local_port}/health",
            "log": str(log_path),
            "stop_file": str(stop_file),
        },
    }
    shared._write_new(activation_path, activation)
    print(json.dumps(activation, sort_keys=True))


if __name__ == "__main__":
    main()
