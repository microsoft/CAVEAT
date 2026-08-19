#!/usr/bin/env python3
"""Publish the sealed two-microcheckpoint Step35 B development candidate."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

import release_c2_paired_candidate as base

NAMESPACE = "bonete61"
JOB = "t-yuxuanli-hpt-c2-step35-micro-candidate-w2"
POD = JOB + "-master-0"
SOURCE = Path(
    "/data/runs/t-yuxuanli/"
    "t-yuxuanli-hpt-c2-step35-micro-candidate-w1-20260816/"
    "source_eval_step35_micro_profile_v1"
)
SOURCE_GIT_SHA = "479d442e348b732f68b4c83fc28e35c97ee84806"
SOURCE_IDENTITY = {
    "files": 95,
    "bytes": 2_217_182,
    "tree_sha256": "d7932e3f0535dbed8bb558dee613a422001a9809b2e2e70627252f900fa3eac4",
}
ENTRYPOINT = SOURCE / "scripts/run_step35_micro_profile_candidate_serve.sh"
ENTRYPOINT_SHA256 = "f9c12e3c7878fd59070c71d4a7812bc16460948e831d0846c0dd26b6eeb0ae9e"
PLAN = Path(
    "/data/runs/t-yuxuanli/"
    "t-yuxuanli-hpt-c2-step35-proximal-b-training-w1-20260815/"
    "step35_proximal_b_run_r4/training/plan.json"
)
PLAN_FILE_SHA256 = "6e77c8704ea989f863e9d99042bdf7a94232efa8e0f63df821edb01a853098f7"
PLAN_BODY_SHA256 = "936f9e40af45097cf54b2949e50848567428cb55455091c55d5f20b7b792c078"
TRAINER_SOURCE = Path(
    "/data/runs/t-yuxuanli/"
    "t-yuxuanli-hpt-c2-step35-proximal-b-training-w1-20260815/"
    "staged_source_da796b4199bc52f4a281e56bc71a922399f1230f"
)
TRAINER_GIT_SHA = "da796b4199bc52f4a281e56bc71a922399f1230f"
TRAINER_TREE_SHA256 = "7e795bacdf1c89d631cd1480ba2dbbb464a4d8680408800c2a01ecc1a6302059"
TRAINER_FILES = 366
TRAINER_BYTES = 46_553_304
RECEIPT = PLAN.parent / "training_receipt.json"
RECEIPT_FILE_SHA256 = "738fcc6d30df208e6334cf8830ec1fbf934b7521a296494530d5faed43894281"
RECEIPT_BODY_SHA256 = "deeee4ae85430a992bb9cb3cd10f5155038c463d675b2ae48a87a6e91f8ab0ee"
EXPECTED_ADAPTERS = (
    {
        "step": 33,
        "tree_sha256": "3f02c601ffa6c13fd67c105c33e8adab5df72fd4fc167c52565fbe8702b0355f",
        "alias": (
            "qwen35-browser-action-step35-trajectory-proximal-b-step33-"
            "3f02c601ffa6-exact-lora"
        ),
    },
    {
        "step": 34,
        "tree_sha256": "7564aeea34facf690ebbe1c59c465ea016176f9ca5c1962cf3d58eeae2a87c22",
        "alias": (
            "qwen35-browser-action-step35-trajectory-proximal-b-step34-"
            "7564aeea34fa-exact-lora"
        ),
    },
)
RELEASE = Path(
    "/data/harness-posttrain/4e6c4fe10d62d660f85c1063e3c1cecd0aed6e30/"
    "browser_action_next_iteration_reservations/20260813T1108Z/"
    "c2_step35_micro_candidate_serve_release_w2.json"
)
HANDOFF_SCHEMA = "c2-step35-micro-candidate-handoff.v1"
LOCAL_PORT = 18556
PROFILE = "B"

BINDING_ARGS = [
    "run-server",
    "--artifact-mode",
    "final_receipt",
    "--usage-class",
    "development_probe_inference_only",
    "--profile",
    "B",
    "--receipt",
    str(RECEIPT),
    "--receipt-file-sha256",
    RECEIPT_FILE_SHA256,
    "--receipt-body-sha256",
    RECEIPT_BODY_SHA256,
    "--plan",
    str(PLAN),
    "--plan-file-sha256",
    PLAN_FILE_SHA256,
    "--plan-body-sha256",
    PLAN_BODY_SHA256,
    "--trainer-source",
    str(TRAINER_SOURCE),
    "--trainer-git-sha",
    TRAINER_GIT_SHA,
    "--trainer-tree-sha256",
    TRAINER_TREE_SHA256,
    "--trainer-files",
    str(TRAINER_FILES),
    "--trainer-bytes",
    str(TRAINER_BYTES),
]


def _live_reservation(job_uid: str, pod_uid: str) -> None:
    job = json.loads(
        subprocess.check_output(
            ["kubectl", "-n", NAMESPACE, "get", "vcjob", JOB, "-o", "json"]
        )
    )
    pod = json.loads(
        subprocess.check_output(
            ["kubectl", "-n", NAMESPACE, "get", "pod", POD, "-o", "json"]
        )
    )
    statuses = pod.get("status", {}).get("containerStatuses") or []
    if (
        job.get("metadata", {}).get("uid") != job_uid
        or pod.get("metadata", {}).get("uid") != pod_uid
        or pod.get("status", {}).get("phase") != "Running"
        or len(statuses) != 1
        or statuses[0].get("restartCount") != 0
    ):
        raise RuntimeError("Step35 B profile reservation identity/state changed")


def _source_descriptor() -> None:
    descriptor = json.loads(
        base.remote_bytes(SOURCE.with_name(SOURCE.name + ".identity.json"))
    )
    relative = ENTRYPOINT.relative_to(SOURCE).as_posix()
    if (
        descriptor.get("status") != "staged_read_only"
        or descriptor.get("root") != str(SOURCE)
        or descriptor.get("git_sha") != SOURCE_GIT_SHA
        or any(descriptor.get(key) != value for key, value in SOURCE_IDENTITY.items())
        or (descriptor.get("required_sha256") or {}).get(relative)
        != ENTRYPOINT_SHA256
    ):
        raise RuntimeError("Step35 serving source identity changed")


def _remote_validate() -> dict[str, Any]:
    arguments = BINDING_ARGS.copy()
    arguments[0] = "validate-profile"
    output = base.kube(
        "env",
        f"PYTHONPATH={SOURCE / 'src'}",
        "/opt/prime-rl/.venv/bin/python",
        "-m",
        "harness_posttrain_eval.step35_micro_profile_candidate_serve",
        *arguments,
    )
    value = json.loads(output)
    observed = [
        {
            "step": item.get("step"),
            "tree_sha256": item.get("tree_sha256"),
            "alias": item.get("alias"),
        }
        for item in value.get("adapters") or []
    ]
    if (
        value.get("valid") is not True
        or value.get("artifact_mode") != "final_receipt"
        or value.get("training_use") is not False
        or value.get("broader_evaluation_authorized") is not True
        or value.get("profile") != PROFILE
        or value.get("steps") != [item["step"] for item in EXPECTED_ADAPTERS]
        or observed != list(EXPECTED_ADAPTERS)
    ):
        raise RuntimeError("Step35 full-profile validation changed")
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--publish", action="store_true", required=True)
    parser.add_argument("--job-uid", required=True)
    parser.add_argument("--pod-uid", required=True)
    parser.add_argument("--handoff-output", type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.handoff_output.exists():
        parser.error(f"handoff already exists: {arguments.handoff_output}")

    base.NAMESPACE = NAMESPACE
    base.JOB = JOB
    base.POD = POD
    base.POD_UID = arguments.pod_uid
    _live_reservation(arguments.job_uid, arguments.pod_uid)
    _source_descriptor()
    validated = _remote_validate()
    core: dict[str, Any] = {
        "schema": "harness-posttrain.browser-action-next-iteration.c2-candidate-serve-release.v1",
        "status": "released",
        "purpose": "c2_candidate_serve",
        "laptop_r01_outcomes_read": False,
        "office_chair_outcomes_read": False,
        "reservation": {"job_name": JOB, "pod_name": POD, "pod_uid": arguments.pod_uid},
        "source": {"root": str(SOURCE), "git_sha": SOURCE_GIT_SHA, **SOURCE_IDENTITY},
        "entrypoint": {"path": str(ENTRYPOINT), "sha256": ENTRYPOINT_SHA256},
        "argv": [ENTRYPOINT_SHA256, *BINDING_ARGS],
    }
    release = {**core, "release_sha256": base.digest(core)}
    base.atomic_remote_write(
        RELEASE, json.dumps(release, sort_keys=True, indent=2).encode() + b"\n"
    )
    candidate_composite = base.digest(
        {"profile": PROFILE, "adapters": validated["adapters"]}
    )
    handoff = {
        "schema": HANDOFF_SCHEMA,
        "status": "published_after_successful_receipt",
        "development_probe_only": True,
        "training_eligible": False,
        "broader_evaluation_authorized": True,
        "profile": PROFILE,
        "steps": [item["step"] for item in EXPECTED_ADAPTERS],
        "aliases": [item["alias"] for item in EXPECTED_ADAPTERS],
        "alias": EXPECTED_ADAPTERS[0]["alias"],
        "receipt_path": str(RECEIPT),
        "receipt_file_sha256": RECEIPT_FILE_SHA256,
        "receipt_body_sha256": RECEIPT_BODY_SHA256,
        "candidate_composite_sha256": candidate_composite,
        "release_path": str(RELEASE),
        "release_body_sha256": release["release_sha256"],
        "serve_job": JOB,
        "serve_job_uid": arguments.job_uid,
        "serve_pod": POD,
        "serve_pod_uid": arguments.pod_uid,
        "local_base_url": f"http://127.0.0.1:{LOCAL_PORT}/v1",
        "validated_profile": validated,
    }
    base.atomic_local_write(
        arguments.handoff_output,
        json.dumps(handoff, sort_keys=True, indent=2).encode() + b"\n",
    )
    print(json.dumps(handoff, sort_keys=True))


if __name__ == "__main__":
    main()
