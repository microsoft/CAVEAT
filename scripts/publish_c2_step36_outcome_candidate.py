#!/usr/bin/env python3
"""Publish one receipt/inventory-bound Step36 development server generation."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import c2_step36_outcome_candidate_contract as contract
import release_c2_paired_candidate as base


def _remote_document(
    path: Path, *, schema: str, hash_field: str, label: str
) -> tuple[bytes, dict[str, Any], str, str]:
    payload = base.remote_bytes(path)
    value = json.loads(payload)
    if not isinstance(value, dict) or value.get("schema") != schema:
        raise RuntimeError(f"{label} schema changed")
    body_sha256 = base.validate_canonical_body(value, hash_field, label)
    return payload, value, hashlib.sha256(payload).hexdigest(), body_sha256


def _reservation(job_uid: str, pod_uid: str, stage: int) -> None:
    job_name = contract.JOB_BY_STAGE[stage]
    pod_name = contract.POD_BY_STAGE[stage]
    job = json.loads(
        subprocess.check_output(
            ["kubectl", "-n", contract.NAMESPACE, "get", "vcjob", job_name, "-o", "json"]
        )
    )
    pod = json.loads(
        subprocess.check_output(
            ["kubectl", "-n", contract.NAMESPACE, "get", "pod", pod_name, "-o", "json"]
        )
    )
    statuses = pod.get("status", {}).get("containerStatuses") or []
    tasks = job.get("spec", {}).get("tasks") or []
    containers = (
        tasks[0].get("template", {}).get("spec", {}).get("containers") or []
        if len(tasks) == 1
        else []
    )
    expected_args = [
        contract.RESERVATION_ENTRYPOINT,
        contract.RESERVATION_ENTRYPOINT_SHA256,
        str(contract.RELEASE_BY_STAGE[stage]),
        contract.RELEASE_SCHEMA,
        "c2_candidate_serve",
        job_name,
        str(contract.RESERVATION_TIMEOUT_SECONDS),
    ]
    if (
        job.get("metadata", {}).get("uid") != job_uid
        or pod.get("metadata", {}).get("uid") != pod_uid
        or pod.get("status", {}).get("phase") != "Running"
        or len(statuses) != 1
        or statuses[0].get("restartCount") != 0
        or len(containers) != 1
        or containers[0].get("image") != contract.RESERVATION_IMAGE
        or containers[0].get("args") != expected_args
    ):
        raise RuntimeError("Step36 reservation identity/state/argv changed")


def _source_descriptor() -> None:
    path = contract.SERVE_SOURCE.with_name(contract.SERVE_SOURCE.name + ".identity.json")
    value = json.loads(base.remote_bytes(path))
    required = value.get("required_sha256") or {}
    if (
        value.get("status") != "staged_read_only"
        or value.get("root") != str(contract.SERVE_SOURCE)
        or value.get("git_sha") != contract.SERVE_SOURCE_GIT_SHA
        or value.get("files") != contract.SERVE_SOURCE_FILES
        or value.get("bytes") != contract.SERVE_SOURCE_BYTES
        or value.get("tree_sha256") != contract.SERVE_SOURCE_TREE_SHA256
        or required.get("scripts/run_step35_micro_profile_candidate_serve.sh")
        != contract.SERVE_ENTRYPOINT_SHA256
        or required.get(
            "src/harness_posttrain_eval/step35_micro_profile_candidate_serve.py"
        )
        != contract.SERVE_MODULE_SHA256
    ):
        raise RuntimeError("proven Step35 serving source identity changed")


def _trainer_descriptor() -> dict[str, Any]:
    return {
        "root": str(contract.TRAINER_SOURCE.resolve()),
        "git_sha": contract.TRAINER_GIT_SHA,
        "path": str(contract.TRAINER_SOURCE.resolve()),
        "files": contract.TRAINER_FILES,
        "bytes": contract.TRAINER_BYTES,
        "tree_sha256": contract.TRAINER_TREE_SHA256,
        "runner": str(contract.TRAINER_RUNNER.resolve()),
        "runner_sha256": contract.TRAINER_RUNNER_SHA256,
    }


def _binding(stage: int) -> tuple[list[str], list[dict[str, Any]]]:
    _plan_payload, plan, plan_file, plan_body = _remote_document(
        contract.PLAN,
        schema=contract.PLAN_SCHEMA,
        hash_field="plan_body_sha256",
        label="Step36 plan",
    )
    if (
        plan_file != contract.PLAN_FILE_SHA256
        or plan_body != contract.PLAN_BODY_SHA256
        or plan.get("status") != "prepared"
        or plan.get("profile") != contract.PROFILE
        or plan.get("artifact_git_sha") != contract.TRAINER_GIT_SHA
        or Path(str(plan.get("training_output", ""))).resolve()
        != contract.TRAINING_OUTPUT
    ):
        raise RuntimeError("exact Step36 plan binding changed")
    arguments = [
        "run-server",
        "--artifact-mode",
        "final_receipt" if stage == 35 else "pre_final_inventory",
        "--usage-class",
        "development_probe_inference_only",
        "--profile",
        contract.PROFILE,
        "--plan",
        str(contract.PLAN),
        "--plan-file-sha256",
        contract.PLAN_FILE_SHA256,
        "--plan-body-sha256",
        contract.PLAN_BODY_SHA256,
        "--trainer-source",
        str(contract.TRAINER_SOURCE),
        "--trainer-git-sha",
        contract.TRAINER_GIT_SHA,
        "--trainer-tree-sha256",
        contract.TRAINER_TREE_SHA256,
        "--trainer-files",
        str(contract.TRAINER_FILES),
        "--trainer-bytes",
        str(contract.TRAINER_BYTES),
    ]
    artifacts: list[dict[str, Any]] = []
    if stage == 35:
        _payload, receipt, file_sha, body_sha = _remote_document(
            contract.RECEIPT,
            schema=contract.RECEIPT_SCHEMA,
            hash_field="receipt_body_sha256",
            label="Step36 training receipt",
        )
        if (
            receipt.get("status") != "ok"
            or receipt.get("profile") != contract.PROFILE
            or receipt.get("artifact_source_git_sha") != contract.TRAINER_GIT_SHA
            or receipt.get("execution_source_git_sha") != contract.TRAINER_GIT_SHA
            or Path(str(receipt.get("plan_path", ""))).resolve() != contract.PLAN
            or receipt.get("plan_sha256") != contract.PLAN_FILE_SHA256
            or receipt.get("plan_body_sha256") != contract.PLAN_BODY_SHA256
            or receipt.get("update_steps") != list(contract.STEPS)
        ):
            raise RuntimeError("Step36 complete training receipt changed")
        arguments += [
            "--receipt",
            str(contract.RECEIPT),
            "--receipt-file-sha256",
            file_sha,
            "--receipt-body-sha256",
            body_sha,
        ]
        artifacts.append(
            {
                "kind": "training_receipt",
                "path": str(contract.RECEIPT),
                "file_sha256": file_sha,
                "body_sha256": body_sha,
            }
        )
    else:
        for step in contract.served_steps(stage):
            path = contract.inventory_path(step)
            _payload, inventory, file_sha, body_sha = _remote_document(
                path,
                schema=contract.MICRO_INVENTORY_SCHEMA,
                hash_field="inventory_body_sha256",
                label=f"Step36 step-{step} micro inventory",
            )
            if (
                inventory.get("status") != "sealed"
                or inventory.get("profile") != contract.PROFILE
                or inventory.get("step") != step
                or inventory.get("development_probe_only") is not True
                or inventory.get("training_eligible") is not False
                or inventory.get("plan_file_sha256") != contract.PLAN_FILE_SHA256
                or inventory.get("plan_body_sha256") != contract.PLAN_BODY_SHA256
                or inventory.get("step36_trainer_source") != _trainer_descriptor()
            ):
                raise RuntimeError(f"Step36 step-{step} inventory binding changed")
            arguments += ["--micro-inventory", str(path)]
            arguments += ["--micro-inventory-file-sha256", file_sha]
            arguments += ["--micro-inventory-body-sha256", body_sha]
            artifacts.append(
                {
                    "step": step,
                    "kind": "microcheckpoint_inventory",
                    "path": str(path),
                    "file_sha256": file_sha,
                    "body_sha256": body_sha,
                }
            )
    return arguments, artifacts


def _remote_validate(arguments: list[str], stage: int) -> dict[str, Any]:
    check = arguments.copy()
    check[0] = "validate-profile"
    output = base.kube(
        "env",
        f"PYTHONPATH={contract.SERVE_SOURCE / 'src'}",
        "/opt/prime-rl/.venv/bin/python",
        "-m",
        "harness_posttrain_eval.step35_micro_profile_candidate_serve",
        *check,
    )
    value = json.loads(output)
    adapters = value.get("adapters") or []
    steps = list(contract.served_steps(stage))
    if (
        value.get("valid") is not True
        or value.get("usage_class") != "development_probe_inference_only"
        or value.get("training_use") is not False
        or value.get("profile") != contract.PROFILE
        or value.get("steps") != steps
        or len(adapters) != len(steps)
        or any(
            item.get("alias")
            != contract.exact_alias(step, str(item.get("tree_sha256", "")))
            for step, item in zip(steps, adapters, strict=True)
        )
        or (stage == 35 and value.get("complete_profile") is not True)
        or (stage < 35 and value.get("broader_evaluation_authorized") is not False)
    ):
        raise RuntimeError("Step36 remote profile validation changed")
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--publish", action="store_true", required=True)
    parser.add_argument("--stage", type=int, choices=contract.STEPS, required=True)
    parser.add_argument("--job-uid", required=True)
    parser.add_argument("--pod-uid", required=True)
    parser.add_argument("--handoff-output", type=Path)
    arguments = parser.parse_args()
    stage = arguments.stage
    handoff_path = (arguments.handoff_output or contract.HANDOFF_BY_STAGE[stage]).resolve()
    if handoff_path != contract.HANDOFF_BY_STAGE[stage] or handoff_path.exists():
        parser.error("handoff output is not the fresh planned create-once path")

    base.NAMESPACE = contract.NAMESPACE
    base.JOB = contract.JOB_BY_STAGE[stage]
    base.POD = contract.POD_BY_STAGE[stage]
    base.POD_UID = arguments.pod_uid
    _reservation(arguments.job_uid, arguments.pod_uid, stage)
    _source_descriptor()
    binding, artifacts = _binding(stage)
    validated = _remote_validate(binding, stage)

    core: dict[str, Any] = {
        "schema": contract.RELEASE_SCHEMA,
        "status": "released",
        "purpose": "c2_candidate_serve",
        "laptop_r01_outcomes_read": False,
        "office_chair_outcomes_read": False,
        "reservation": {
            "job_name": contract.JOB_BY_STAGE[stage],
            "pod_name": contract.POD_BY_STAGE[stage],
            "pod_uid": arguments.pod_uid,
        },
        "source": {
            "root": str(contract.SERVE_SOURCE),
            "git_sha": contract.SERVE_SOURCE_GIT_SHA,
            "files": contract.SERVE_SOURCE_FILES,
            "bytes": contract.SERVE_SOURCE_BYTES,
            "tree_sha256": contract.SERVE_SOURCE_TREE_SHA256,
        },
        "entrypoint": {
            "path": str(contract.SERVE_ENTRYPOINT),
            "sha256": contract.SERVE_ENTRYPOINT_SHA256,
        },
        "argv": [contract.SERVE_ENTRYPOINT_SHA256, *binding],
    }
    release = {**core, "release_sha256": contract.digest(core)}
    base.atomic_remote_write(
        contract.RELEASE_BY_STAGE[stage],
        json.dumps(release, sort_keys=True, indent=2).encode() + b"\n",
    )
    adapters = validated["adapters"]
    composite = contract.digest({"profile": contract.PROFILE, "adapters": adapters})
    handoff = {
        "schema": contract.HANDOFF_SCHEMA,
        "status": (
            "published_after_successful_receipt"
            if stage == 35
            else "published_after_microcheckpoint_inventory"
        ),
        "development_probe_only": True,
        "training_eligible": False,
        "broader_evaluation_authorized": False,
        "full_evaluation_authorized": False,
        "strict_improvement_required_before_full_evaluation": True,
        "profile": contract.PROFILE,
        "stage": stage,
        "steps": list(contract.served_steps(stage)),
        "aliases": [item["alias"] for item in adapters],
        "alias": adapters[0]["alias"],
        "candidate_tree_sha256": adapters[0]["tree_sha256"],
        "candidate_composite_sha256": composite,
        "candidate_artifacts": artifacts,
        "release_path": str(contract.RELEASE_BY_STAGE[stage]),
        "release_body_sha256": release["release_sha256"],
        "serve_job": contract.JOB_BY_STAGE[stage],
        "serve_job_uid": arguments.job_uid,
        "serve_pod": contract.POD_BY_STAGE[stage],
        "serve_pod_uid": arguments.pod_uid,
        "local_base_url": f"http://127.0.0.1:{contract.LOCAL_PORT_BY_STAGE[stage]}/v1",
        "validated_profile": validated,
    }
    if stage == 35:
        handoff.update(
            {
                "receipt_path": artifacts[0]["path"],
                "receipt_file_sha256": artifacts[0]["file_sha256"],
                "receipt_body_sha256": artifacts[0]["body_sha256"],
            }
        )
    base.atomic_local_write(
        handoff_path, json.dumps(handoff, sort_keys=True, indent=2).encode() + b"\n"
    )
    print(json.dumps(handoff, sort_keys=True))


if __name__ == "__main__":
    main()
