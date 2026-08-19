#!/usr/bin/env python3
"""Publish the reserved adaptive candidate only after its exact receipt exists."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any


NAMESPACE = "bonete61"
POD = "t-yuxuanli-hpt-c2-adaptive-candidate-w1-master-0"
POD_UID = "66fb6652-f8c4-49a4-9c3d-d23709e15b88"
JOB = "t-yuxuanli-hpt-c2-adaptive-candidate-w1"
RECEIPT = Path(
    "/data/runs/t-yuxuanli/"
    "t-yuxuanli-hpt-c2-adaptive-buynow-sft-w1-20260815/"
    "training_r1/training/training_receipt.json"
)
SOURCE = Path(
    "/data/runs/t-yuxuanli/"
    "t-yuxuanli-hpt-c2-adaptive-buynow-sft-w1-20260815/"
    "source_eval_adaptive_serve_streamlined_v1"
)
ENTRYPOINT = SOURCE / "scripts/run_adaptive_buy_now_candidate_serve.sh"
ENTRYPOINT_SHA256 = "ccc8c31c2238fad181d43109b54f963a9db7536d5e35fadca856776e15dbf3cd"
SOURCE_GIT_SHA = "b6896198af77f6f309be428f2b770a57f87d4912"
SOURCE_IDENTITY = {
    "files": 89,
    "bytes": 2_165_025,
    "tree_sha256": "50791163e01f230f796cdb9149e02c1c2cbbb48143ae3c3336d2939d0c4dbb0c",
}
TRAINER = Path(
    "/data/runs/t-yuxuanli/"
    "t-yuxuanli-hpt-c2-adaptive-buynow-sft-w1-20260815/"
    "source_distill_50e39b02"
)
TRAINER_GIT_SHA = "50e39b02545334a02bf4ade35d724000f173288e"
TRAINER_TREE = "c9e1561c37c50387ee6a9e8caac87216e19f6c77af1c348604fede81059ffd75"
RELEASE = Path(
    "/data/harness-posttrain/4e6c4fe10d62d660f85c1063e3c1cecd0aed6e30/"
    "browser_action_next_iteration_reservations/20260813T1108Z/"
    "c2_adaptive_candidate_serve_release_w1.json"
)
PARENT_TREE = "5939382fbc6db775972dc9cebf3e5be20654149a0412b4f00072bad12a2215ca"
TOKENIZER = "06b9509352d2af50381ab2247e083b80d32d5c0aba91c272ca9ff729b6a0e523"
CHAT_TEMPLATE = "a4aee8afcf2e0711942cf848899be66016f8d14a889ff9ede07bca099c28f715"


REMOTE_CREATE_ONLY_PROGRAM = r"""
import hashlib
import json
import os
import pathlib
import sys

target = pathlib.Path(sys.argv[1])
temporary = target.with_name(target.name + ".tmp." + sys.argv[2])
expected_size = int(sys.argv[3])
expected_sha256 = sys.argv[4]
payload = sys.stdin.buffer.read()
actual_sha256 = hashlib.sha256(payload).hexdigest()
if len(payload) != expected_size or actual_sha256 != expected_sha256:
    raise RuntimeError(
        f"stdin identity mismatch: got {len(payload)} bytes/{actual_sha256}, "
        f"expected {expected_size}/{expected_sha256}"
    )

descriptor = os.open(
    temporary,
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
    0o600,
)
try:
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.link(temporary, target, follow_symlinks=False)
    directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    published = target.lstat()
    staged = temporary.lstat()
    if (
        not os.path.isfile(target)
        or published.st_dev != staged.st_dev
        or published.st_ino != staged.st_ino
        or published.st_size != expected_size
    ):
        raise RuntimeError("published target is not the staged complete inode")
    print(json.dumps({"bytes": expected_size, "sha256": expected_sha256}))
finally:
    try:
        temporary.unlink()
    except FileNotFoundError:
        pass
"""


def canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def validate_canonical_body(value: dict[str, Any], field: str, label: str) -> str:
    claimed = value.get(field)
    if not isinstance(claimed, str) or len(claimed) != 64:
        raise ValueError(f"{label} body SHA-256 is malformed")
    body = {key: item for key, item in value.items() if key != field}
    if digest(body) != claimed:
        raise ValueError(f"{label} canonical body SHA-256 changed")
    return claimed


def kube(*argv: str, input_bytes: bytes | None = None) -> bytes:
    stdin_flag = ["-i"] if input_bytes is not None else []
    return subprocess.check_output(
        ["kubectl", "-n", NAMESPACE, "exec", *stdin_flag, POD, "--", *argv],
        input=input_bytes,
    )


def remote_bytes(path: Path) -> bytes:
    return kube(
        "python3",
        "-c",
        "import pathlib,sys;sys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes())",
        str(path),
    )


def atomic_remote_write(path: Path, payload: bytes) -> None:
    if not payload:
        raise ValueError("refusing to publish an empty release payload")
    expected_sha256 = hashlib.sha256(payload).hexdigest()
    result = kube(
        "python3",
        "-c",
        REMOTE_CREATE_ONLY_PROGRAM,
        str(path),
        POD_UID,
        str(len(payload)),
        expected_sha256,
        input_bytes=payload,
    )
    published = json.loads(result)
    if published != {"bytes": len(payload), "sha256": expected_sha256}:
        raise RuntimeError(f"remote publication acknowledgement differs: {published!r}")


def atomic_local_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    if path.exists():
        raise FileExistsError(path)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    if path.exists():
        raise FileExistsError(path)
    os.rename(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--publish", action="store_true", required=True)
    parser.add_argument("--handoff-output", type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.handoff_output.exists():
        parser.error(f"handoff already exists: {arguments.handoff_output}")

    receipt_bytes = remote_bytes(RECEIPT)
    receipt = json.loads(receipt_bytes)
    candidate = receipt.get("candidate") or {}
    receipt_file = hashlib.sha256(receipt_bytes).hexdigest()
    receipt_body = validate_canonical_body(
        receipt, "receipt_body_sha256", "adaptive training receipt"
    )
    plan_path = RECEIPT.with_name("plan.json")
    plan_bytes = remote_bytes(plan_path)
    plan = json.loads(plan_bytes)
    plan_body = validate_canonical_body(plan, "plan_body_sha256", "adaptive plan")
    if (
        receipt.get("schema")
        != "harness-distill.adaptive-buy-now-chosen-ce-receipt.v1"
        or receipt.get("status") != "ok"
        or receipt.get("scientific_label")
        != "adaptive_same_state_buy_now_chosen_ce_from_sealed_step25"
        or receipt.get("artifact_source_git_sha") != TRAINER_GIT_SHA
        or receipt.get("execution_source_git_sha") != TRAINER_GIT_SHA
        or receipt.get("source_step") != 25
        or receipt.get("final_step") != 26
        or receipt.get("optimizer_updates") != 1
        or receipt.get("learning_rate") != 2e-6
        or receipt.get("collection_counts")
        != {
            "evaluation": 0,
            "executed": 8,
            "heldout": 0,
            "phases": {"premature_buy_now_correction": 8},
            "retention": 0,
            "total": 8,
            "unique_states": 8,
        }
        or Path(str(receipt.get("plan_path", ""))) != plan_path
        or receipt.get("plan_sha256") != hashlib.sha256(plan_bytes).hexdigest()
        or receipt.get("plan_body_sha256") != plan_body
        or plan.get("schema") != "harness-distill.adaptive-buy-now-chosen-ce-plan.v1"
        or plan.get("status") != "prepared"
        or plan.get("scientific_label")
        != "adaptive_same_state_buy_now_chosen_ce_from_sealed_step25"
        or plan.get("artifact_git_sha") != TRAINER_GIT_SHA
        or plan.get("source_step") != 25
        or plan.get("final_step") != 26
        or plan.get("optimizer_updates") != 1
        or plan.get("learning_rate") != 2e-6
        or plan.get("collection_counts") != receipt.get("collection_counts")
        or Path(str(plan.get("candidate_path", "")))
        != RECEIPT.parent / "prime_output/weights/step_26/lora_adapters"
        or candidate.get("name") != "step26-action-weighted-ce"
        or candidate.get("update") != 26
        or not isinstance(receipt_body, str)
        or len(receipt_body) != 64
    ):
        parser.error("adaptive training receipt is not the expected successful artifact")
    candidate_tree = candidate["tree_sha256"]
    adapter_config = candidate["adapter_config_sha256"]
    stable_marker = candidate["stable_marker_sha256"]
    alias = (
        "qwen35-browser-action-step26-adaptive-buynow-ce-"
        f"{candidate_tree[:12]}-exact-lora"
    )
    composite = digest(
        {
            "schema": "harness-posttrain.exact-lora-composite.v1",
            "execution": "peft_unmerged_exact_lora",
            "parent_tree_sha256": PARENT_TREE,
            "adapter_tree_sha256": candidate_tree,
            "adapter_config_sha256": adapter_config,
            "tokenizer_json_sha256": TOKENIZER,
            "chat_template_sha256": CHAT_TEMPLATE,
            "dtype": "bfloat16",
        }
    )
    core = {
        "schema": "harness-posttrain.browser-action-next-iteration.c2-candidate-serve-release.v1",
        "status": "released",
        "purpose": "c2_candidate_serve",
        "laptop_r01_outcomes_read": False,
        "office_chair_outcomes_read": False,
        "reservation": {
            "job_name": JOB,
            "pod_name": POD,
            "pod_uid": POD_UID,
        },
        "source": {
            "root": str(SOURCE),
            "git_sha": SOURCE_GIT_SHA,
            **SOURCE_IDENTITY,
        },
        "entrypoint": {
            "path": str(ENTRYPOINT),
            "sha256": ENTRYPOINT_SHA256,
        },
        "argv": [
            ENTRYPOINT_SHA256,
            str(RECEIPT),
            receipt_file,
            receipt_body,
            candidate_tree,
            adapter_config,
            stable_marker,
            str(TRAINER),
            TRAINER_GIT_SHA,
            TRAINER_TREE,
            JOB,
            POD,
            POD_UID,
        ],
    }
    release = {**core, "release_sha256": digest(core)}
    payload = json.dumps(release, sort_keys=True, indent=2).encode() + b"\n"
    atomic_remote_write(RELEASE, payload)
    handoff = {
        "schema": "c2-adaptive-candidate-release-handoff.v1",
        "status": "published_after_successful_receipt",
        "receipt_path": str(RECEIPT),
        "receipt_file_sha256": receipt_file,
        "receipt_body_sha256": receipt_body,
        "candidate_tree_sha256": candidate_tree,
        "candidate_composite_sha256": composite,
        "alias": alias,
        "release_path": str(RELEASE),
        "release_body_sha256": release["release_sha256"],
        "serve_job": JOB,
        "serve_pod": POD,
        "serve_pod_uid": POD_UID,
        "local_base_url": "http://127.0.0.1:18545/v1",
    }
    handoff_payload = json.dumps(handoff, sort_keys=True, indent=2).encode() + b"\n"
    atomic_local_write(arguments.handoff_output, handoff_payload)
    print(json.dumps(handoff, sort_keys=True))


if __name__ == "__main__":
    main()
