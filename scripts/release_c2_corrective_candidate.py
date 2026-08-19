#!/usr/bin/env python3
"""Publish the already-reserved corrective candidate only after its receipt exists."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any


NAMESPACE = "bonete61"
POD = "t-yuxuanli-hpt-c2-corrective-candidate-w3-master-0"
POD_UID = "e2282cb2-3f78-465d-a0bb-9c6d804d37e3"
JOB = "t-yuxuanli-hpt-c2-corrective-candidate-w3"
RECEIPT = Path(
    "/data/runs/t-yuxuanli/"
    "t-yuxuanli-hpt-c2-macro-recovery-ce-w1-20260815/"
    "training_r4/training/training_receipt.json"
)
SOURCE = Path(
    "/data/runs/t-yuxuanli/"
    "t-yuxuanli-hpt-c2-macro-recovery-ce-w1-20260815/"
    "source_eval_0e29502d"
)
ENTRYPOINT = SOURCE / "scripts/run_reachability_corrective_candidate_serve.sh"
ENTRYPOINT_SHA256 = "5511154b13ea751918f1359957f45c1d90057c278efb692f738d2810601030bf"
SOURCE_GIT_SHA = "0e29502db3821ea2803ad3b0108f996c9d841627"
SOURCE_IDENTITY = {
    "files": 86,
    "bytes": 2_151_399,
    "tree_sha256": "c633ff4b800988e8a8c6d6f758f84ffca67660372c1a8a4b687a0b3bfe269529",
}
TRAINER = Path(
    "/data/runs/t-yuxuanli/"
    "t-yuxuanli-hpt-c2-macro-recovery-ce-w1-20260815/"
    "source_distill_c29787b5"
)
TRAINER_GIT_SHA = "c29787b5e9ec2e49930a1ae4aabaced0966baf8b"
TRAINER_TREE = "b025755dd31d297199e0e140e3ce9a2d8f5c84ee9b351802a6e3e7cee365f919"
RELEASE = Path(
    "/data/harness-posttrain/4e6c4fe10d62d660f85c1063e3c1cecd0aed6e30/"
    "browser_action_next_iteration_reservations/20260813T1108Z/"
    "c2_corrective_candidate_serve_release_w3.json"
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
    # Hard-link creation is the only publication point.  It is atomic and
    # create-only: EEXIST is a hard failure and no prior target is replaced.
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


def kube(*argv: str, input_bytes: bytes | None = None) -> bytes:
    # ``kubectl exec`` does not attach stdin unless ``-i`` is present.  Omitting
    # it silently turns a perfectly good local payload into EOF in the remote
    # process, which can publish a zero-byte file.  Only request stdin for the
    # one operation that actually sends bytes.
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
    """Create ``path`` without replacement and expose only complete bytes."""

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
    receipt_body = receipt.get("receipt_body_sha256")
    if (
        receipt.get("schema") != "harness-distill.macro-recovery-ce-receipt.v1"
        or receipt.get("status") != "ok"
        or receipt.get("scientific_label")
        != "on_policy_reachability_corrective_reasoning_weighted_ce"
        or receipt.get("artifact_source_git_sha") != TRAINER_GIT_SHA
        or receipt.get("execution_source_git_sha") != TRAINER_GIT_SHA
        or candidate.get("name") != "step26-reachability-corrective-ce"
        or candidate.get("update") != 26
        or not isinstance(receipt_body, str)
        or len(receipt_body) != 64
    ):
        parser.error("corrective training receipt is not the expected successful artifact")
    candidate_tree = candidate["tree_sha256"]
    adapter_config = candidate["adapter_config_sha256"]
    stable_marker = candidate["stable_marker_sha256"]
    alias = (
        "qwen35-browser-action-step26-reachability-ce-"
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
        "schema": "c2-corrective-candidate-release-handoff.v1",
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
        "local_base_url": "http://127.0.0.1:18544/v1",
    }
    handoff_payload = json.dumps(handoff, sort_keys=True, indent=2).encode() + b"\n"
    atomic_local_write(arguments.handoff_output, handoff_payload)
    print(json.dumps(handoff, sort_keys=True))


if __name__ == "__main__":
    main()
