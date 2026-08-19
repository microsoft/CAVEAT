#!/usr/bin/env python3
"""Atomically publish the sealed Step35 A33 development candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import release_c2_paired_candidate as base

NAMESPACE = "bonete61"
JOB = "t-yuxuanli-hpt-c2-step35-micro-candidate-w1"
POD = JOB + "-master-0"
POD_UID = "1f615eb4-c3e0-4eab-b408-b5d033a4eee8"
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
    "t-yuxuanli-hpt-c2-step35-proximal-a-training-w1-20260815/"
    "step35_proximal_a_run_r3/training/plan.json"
)
PLAN_FILE_SHA256 = "6667e885a7d4cfded801b640563e3782662948bce349ab4f96ad52c0015100e3"
PLAN_BODY_SHA256 = "f265b1e331a438d29bb536c923a60bce6532d8a156b8b395c77288c25feca391"
TRAINER_SOURCE = Path(
    "/data/runs/t-yuxuanli/"
    "t-yuxuanli-hpt-c2-step35-proximal-a-training-w1-20260815/"
    "staged_source_da796b4199bc52f4a281e56bc71a922399f1230f"
)
TRAINER_GIT_SHA = "da796b4199bc52f4a281e56bc71a922399f1230f"
TRAINER_TREE_SHA256 = "7e795bacdf1c89d631cd1480ba2dbbb464a4d8680408800c2a01ecc1a6302059"
TRAINER_FILES = 366
TRAINER_BYTES = 46_553_304
INVENTORY = PLAN.parent / "microcheckpoint_inventories/step_33.json"
INVENTORY_FILE_SHA256 = "43a979e012852b0879b8492eca54948134715965ca88f7073b938192c11aa21d"
INVENTORY_BODY_SHA256 = "f98ec3a9d10c2e67fb4451a551cb86d24c4ed42b90b4d3becf28fed4b6d26fb2"
CANDIDATE_TREE_SHA256 = "e164bf30719dac07e024a89d147b9b22a87a81d646f1a3f9a0cc4fcb53840081"
ALIAS = (
    "qwen35-browser-action-step35-trajectory-proximal-a-step33-"
    "e164bf30719d-exact-lora"
)
RELEASE = Path(
    "/data/harness-posttrain/4e6c4fe10d62d660f85c1063e3c1cecd0aed6e30/"
    "browser_action_next_iteration_reservations/20260813T1108Z/"
    "c2_step35_micro_candidate_serve_release_w1.json"
)
HANDOFF_SCHEMA = "c2-step35-micro-candidate-handoff.v1"
LOCAL_PORT = 18555

BINDING_ARGS = [
    "run-server",
    "--artifact-mode",
    "pre_final_inventory",
    "--usage-class",
    "development_probe_inference_only",
    "--profile",
    "A",
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
    "--micro-inventory",
    str(INVENTORY),
    "--micro-inventory-file-sha256",
    INVENTORY_FILE_SHA256,
    "--micro-inventory-body-sha256",
    INVENTORY_BODY_SHA256,
]


def _live_pod() -> None:
    value = json.loads(
        subprocess.check_output(
            ["kubectl", "-n", NAMESPACE, "get", "pod", POD, "-o", "json"]
        )
    )
    statuses = value.get("status", {}).get("containerStatuses") or []
    if (
        value.get("metadata", {}).get("uid") != POD_UID
        or value.get("status", {}).get("phase") != "Running"
        or len(statuses) != 1
        or statuses[0].get("restartCount") != 0
    ):
        raise RuntimeError("Step35 candidate pod identity/state changed")


def _source_descriptor() -> None:
    descriptor = json.loads(base.remote_bytes(SOURCE.with_name(SOURCE.name + ".identity.json")))
    if (
        descriptor.get("status") != "staged_read_only"
        or descriptor.get("root") != str(SOURCE)
        or descriptor.get("git_sha") != SOURCE_GIT_SHA
        or any(descriptor.get(key) != value for key, value in SOURCE_IDENTITY.items())
        or (descriptor.get("required_sha256") or {}).get(ENTRYPOINT.relative_to(SOURCE).as_posix())
        != ENTRYPOINT_SHA256
    ):
        raise RuntimeError("Step35 serving source identity changed")


def _validate_inventory_bytes() -> None:
    payload = base.remote_bytes(INVENTORY)
    value = json.loads(payload)
    body = base.validate_canonical_body(value, "inventory_body_sha256", "micro inventory")
    candidate = value.get("candidate") or {}
    if (
        hashlib.sha256(payload).hexdigest() != INVENTORY_FILE_SHA256
        or body != INVENTORY_BODY_SHA256
        or value.get("schema") != "harness-posttrain.step35-microcheckpoint-inventory.v1"
        or value.get("status") != "sealed"
        or value.get("profile") != "A"
        or value.get("step") != 33
        or candidate.get("tree_sha256") != CANDIDATE_TREE_SHA256
        or candidate.get("files") != 2
        or candidate.get("bytes") != 933_975_509
    ):
        raise RuntimeError("sealed A33 micro inventory changed")


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
    adapters = value.get("adapters") or []
    if (
        value.get("valid") is not True
        or value.get("artifact_mode") != "pre_final_inventory"
        or value.get("development_probe_only") is not True
        or value.get("training_use") is not False
        or value.get("broader_evaluation_authorized") is not False
        or value.get("profile") != "A"
        or value.get("steps") != [33]
        or len(adapters) != 1
        or adapters[0].get("alias") != ALIAS
        or adapters[0].get("tree_sha256") != CANDIDATE_TREE_SHA256
    ):
        raise RuntimeError("Step35 A33 profile validation changed")
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--publish", action="store_true", required=True)
    parser.add_argument("--handoff-output", type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.handoff_output.exists():
        parser.error(f"handoff already exists: {arguments.handoff_output}")

    base.NAMESPACE = NAMESPACE
    base.JOB = JOB
    base.POD = POD
    base.POD_UID = POD_UID
    _live_pod()
    _source_descriptor()
    _validate_inventory_bytes()
    validated = _remote_validate()
    core: dict[str, Any] = {
        "schema": "harness-posttrain.browser-action-next-iteration.c2-candidate-serve-release.v1",
        "status": "released",
        "purpose": "c2_candidate_serve",
        "laptop_r01_outcomes_read": False,
        "office_chair_outcomes_read": False,
        "reservation": {"job_name": JOB, "pod_name": POD, "pod_uid": POD_UID},
        "source": {"root": str(SOURCE), "git_sha": SOURCE_GIT_SHA, **SOURCE_IDENTITY},
        "entrypoint": {"path": str(ENTRYPOINT), "sha256": ENTRYPOINT_SHA256},
        "argv": [ENTRYPOINT_SHA256, *BINDING_ARGS],
    }
    release = {**core, "release_sha256": base.digest(core)}
    base.atomic_remote_write(
        RELEASE, json.dumps(release, sort_keys=True, indent=2).encode() + b"\n"
    )
    handoff = {
        "schema": HANDOFF_SCHEMA,
        "status": "published_after_microcheckpoint_inventory",
        "development_probe_only": True,
        "training_eligible": False,
        "broader_evaluation_authorized": False,
        "profile": "A",
        "steps": [33],
        "aliases": [ALIAS],
        "alias": ALIAS,
        "inventory_path": str(INVENTORY),
        "inventory_file_sha256": INVENTORY_FILE_SHA256,
        "inventory_body_sha256": INVENTORY_BODY_SHA256,
        "candidate_tree_sha256": CANDIDATE_TREE_SHA256,
        "release_path": str(RELEASE),
        "release_body_sha256": release["release_sha256"],
        "serve_job": JOB,
        "serve_pod": POD,
        "serve_pod_uid": POD_UID,
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
