#!/usr/bin/env python3
"""Publish the reserved paired candidate only after its exact receipt exists."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any


NAMESPACE = "bonete61"
POD = "t-yuxuanli-hpt-c2-paired-candidate-w1-master-0"
POD_UID = "ddc10fee-929f-485d-bdce-daae2b884579"
JOB = "t-yuxuanli-hpt-c2-paired-candidate-w1"
CAMPAIGN_ROOT = Path(
    "/data/runs/t-yuxuanli/t-yuxuanli-hpt-c2-paired-pref-w1-20260815"
)
RECEIPT = CAMPAIGN_ROOT / "training_r1/training/training_receipt.json"
SOURCE = CAMPAIGN_ROOT / "source_eval_paired_serve_v4"
ENTRYPOINT = SOURCE / "scripts/run_paired_buy_now_candidate_serve.sh"
ENTRYPOINT_SHA256 = "8b2c77daf45d1d10a47f75f249900fb2481792032d0f37d9e7887593afa51fd7"
SOURCE_GIT_SHA = "9debfc40d9a08674961ae0fa2c0f7225157a5066"
SOURCE_IDENTITY = {
    "files": 92,
    "bytes": 2_180_159,
    "tree_sha256": "7586f9c0ac1401991f0de4f12b0cdbfb96a9ce6d23633dec78ee88961736adb0",
}
TRAINER = CAMPAIGN_ROOT / "source_distill_65989f69"
TRAINER_GIT_SHA = "65989f69c6380ffbf7805f094cdff3557dfc37e8"
TRAINER_TREE = "5a364c7d12aa6d11d67f8f55419baabcf125a1beabb8263313714f9fe168ee2e"
RELEASE = Path(
    "/data/harness-posttrain/4e6c4fe10d62d660f85c1063e3c1cecd0aed6e30/"
    "browser_action_next_iteration_reservations/20260813T1108Z/"
    "c2_paired_candidate_serve_release_w1.json"
)
PARENT_TREE = "5939382fbc6db775972dc9cebf3e5be20654149a0412b4f00072bad12a2215ca"
TOKENIZER = "06b9509352d2af50381ab2247e083b80d32d5c0aba91c272ca9ff729b6a0e523"
CHAT_TEMPLATE = "a4aee8afcf2e0711942cf848899be66016f8d14a889ff9ede07bca099c28f715"
RECEIPT_SCHEMA = "harness-distill.paired-buy-now-unlikelihood-receipt.v1"
PLAN_SCHEMA = "harness-distill.paired-buy-now-unlikelihood-plan.v1"
SCIENTIFIC_LABEL = "sealed_same_state_paired_chosen_ce_rejected_unlikelihood"
OBJECTIVE = "paired_chosen_ce_plus_bounded_rejected_token_unlikelihood"
OBJECTIVE_COEFFICIENTS = {
    "chosen_pre_action_tail_ce": "40/100",
    "chosen_action_ce": "30/100",
    "rejected_semantic_unlikelihood": "30/100",
}
CUSTOM_LOSS = {
    "import_path": "harness_distill.paired_buy_now_loss.rejected_token_unlikelihood_loss",
    "formula": "-log(1-probability_cap*p_theta(rejected_token|rejected_prefix))",
    "probability_cap": 0.95,
    "precision": "float32",
}
PAIR_COUNTS = {
    "states": 8,
    "chosen": 8,
    "rejected": 8,
    "per_update": 8,
    "updates": 4,
    "evaluation": 0,
    "heldout": 0,
    "variants": {"graded": 2, "graded3": 2, "graded4": 2, "mixed": 2},
}
CANDIDATE_NAME = "step29-paired-buy-now-unlikelihood"
CANDIDATE_PATH = RECEIPT.parent / "prime_output/weights/step_29/lora_adapters"
PRIME_COMMIT = "d334ea52940b47f426293a7d146239e3fbf91caa"


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


def sha(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{label} is not a lowercase SHA-256")
    return value


def validate_canonical_body(value: dict[str, Any], field: str, label: str) -> str:
    claimed = sha(value.get(field), f"{label} body SHA-256")
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
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path, follow_symlinks=False)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def plan_matches(plan: dict[str, Any]) -> bool:
    return (
        plan.get("schema") == PLAN_SCHEMA
        and plan.get("status") == "prepared"
        and plan.get("scientific_label") == SCIENTIFIC_LABEL
        and plan.get("artifact_git_sha") == TRAINER_GIT_SHA
        and plan.get("source_step") == 25
        and plan.get("update_steps") == [26, 27, 28, 29]
        and plan.get("final_step") == 29
        and plan.get("optimizer_updates") == 4
        and plan.get("learning_rate") == 5e-6
        and plan.get("objective") == OBJECTIVE
        and plan.get("objective_coefficients") == OBJECTIVE_COEFFICIENTS
        and plan.get("custom_loss") == CUSTOM_LOSS
        and plan.get("pair_source_kind") == "sealed_exact8_derived_collection_r2"
        and plan.get("frozen_exact8_manifest", {}).get("sha256")
        == "b280515d6772931b20db63ef16436477cd65c67123c495275bdb27db01776124"
        and plan.get("pair_counts") == PAIR_COUNTS
        and plan.get("on_policy") is False
        and plan.get("policy_gradient") is False
        and plan.get("reference_logprobs") is False
        and plan.get("fresh_optimizer") is True
        and plan.get("fresh_scheduler") is True
        and plan.get("fresh_dataloader") is True
        and plan.get("launch_authorized") is True
        and Path(str(plan.get("candidate_path", ""))).resolve() == CANDIDATE_PATH
        and plan.get("candidate_name") == CANDIDATE_NAME
    )


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
        receipt, "receipt_body_sha256", "paired training receipt"
    )
    plan_path = RECEIPT.with_name("plan.json")
    plan_bytes = remote_bytes(plan_path)
    plan = json.loads(plan_bytes)
    plan_body = validate_canonical_body(plan, "plan_body_sha256", "paired plan")
    if (
        receipt.get("schema") != RECEIPT_SCHEMA
        or receipt.get("status") != "ok"
        or receipt.get("scientific_label") != SCIENTIFIC_LABEL
        or receipt.get("artifact_source_git_sha") != TRAINER_GIT_SHA
        or receipt.get("execution_source_git_sha") != TRAINER_GIT_SHA
        or receipt.get("prime_commit") != PRIME_COMMIT
        or receipt.get("source_step") != 25
        or receipt.get("update_steps") != [26, 27, 28, 29]
        or receipt.get("final_step") != 29
        or receipt.get("optimizer_updates") != 4
        or receipt.get("learning_rate") != 5e-6
        or receipt.get("objective") != OBJECTIVE
        or receipt.get("objective_coefficients") != OBJECTIVE_COEFFICIENTS
        or receipt.get("custom_loss") != CUSTOM_LOSS
        or Path(str(receipt.get("plan_path", ""))).resolve() != plan_path
        or receipt.get("plan_sha256") != hashlib.sha256(plan_bytes).hexdigest()
        or receipt.get("plan_body_sha256") != plan_body
        or not plan_matches(plan)
        or candidate.get("name") != CANDIDATE_NAME
        or candidate.get("update") != 29
        or Path(str(candidate.get("path", ""))).resolve() != CANDIDATE_PATH
        or not isinstance(candidate.get("files"), int)
        or candidate.get("files", 0) <= 0
        or not isinstance(candidate.get("bytes"), int)
        or candidate.get("bytes", 0) <= 0
    ):
        parser.error("paired training receipt is not the expected successful artifact")
    candidate_tree = sha(candidate.get("tree_sha256"), "candidate tree")
    adapter_config = sha(candidate.get("adapter_config_sha256"), "adapter config")
    stable_marker = sha(candidate.get("stable_marker_sha256"), "stable marker")
    alias = f"qwen35-browser-action-step29-paired-buynow-{candidate_tree[:12]}-exact-lora"
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
        "reservation": {"job_name": JOB, "pod_name": POD, "pod_uid": POD_UID},
        "source": {"root": str(SOURCE), "git_sha": SOURCE_GIT_SHA, **SOURCE_IDENTITY},
        "entrypoint": {"path": str(ENTRYPOINT), "sha256": ENTRYPOINT_SHA256},
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
        "schema": "c2-paired-candidate-release-handoff.v1",
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
        "local_base_url": "http://127.0.0.1:18546/v1",
    }
    handoff_payload = json.dumps(handoff, sort_keys=True, indent=2).encode() + b"\n"
    atomic_local_write(arguments.handoff_output, handoff_payload)
    print(json.dumps(handoff, sort_keys=True))


if __name__ == "__main__":
    main()
