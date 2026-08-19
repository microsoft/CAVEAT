#!/usr/bin/env python3
"""Atomically bind the reserved paired-preference trainer to sealed artifacts.

This publisher is deliberately parameterized: before the paired trainer source
and plan are sealed there is no release file to publish.  Publication requires
all immutable identities on the command line and exposes the final pathname
only after the complete payload has been written, hashed, and fsynced.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any


NAMESPACE = "bonete61"
JOB = "t-yuxuanli-hpt-c2-paired-pref-w1"
POD = f"{JOB}-master-0"
POD_UID = "f2ebf117-62a6-40a8-b909-0725bb02003b"
CAMPAIGN_ROOT = Path(
    "/data/runs/t-yuxuanli/t-yuxuanli-hpt-c2-paired-pref-w1-20260815"
)
PLAN = CAMPAIGN_ROOT / "training_r1/training/plan.json"
RELEASE = Path(
    "/data/harness-posttrain/4e6c4fe10d62d660f85c1063e3c1cecd0aed6e30/"
    "browser_action_next_iteration_reservations/20260813T1108Z/"
    "c2_paired_pref_training_release_w1.json"
)
RELEASE_SCHEMA = (
    "harness-posttrain.browser-action-next-iteration."
    "c2-paired-pref-training-release.v1"
)
PURPOSE = "c2_paired_pref_training"
RUNNER_RELATIVE = Path("scripts/run_paired_buy_now_training.sh")
PRIME_ROOT = "/opt/prime-rl"
EXPECTED_SOURCE_STEP = 25
EXPECTED_FINAL_STEP = 29
EXPECTED_OPTIMIZER_UPDATES = 4


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
if not payload or len(payload) != expected_size or actual_sha256 != expected_sha256:
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


REMOTE_SOURCE_IDENTITY_PROGRAM = r"""
import hashlib
import json
import os
import pathlib
import stat
import sys

root = pathlib.Path(sys.argv[1])
if root.is_symlink() or not root.is_dir() or not stat.S_ISDIR(root.lstat().st_mode):
    raise RuntimeError("source root is not a safe directory")
rows = []
total = 0
for directory, directory_names, file_names in os.walk(root, followlinks=False):
    base = pathlib.Path(directory)
    for name in directory_names:
        child = base / name
        if child.is_symlink() or not stat.S_ISDIR(child.lstat().st_mode):
            raise RuntimeError(f"unsafe source directory: {child}")
    for name in file_names:
        child = base / name
        if child.is_symlink() or not child.is_file() or not stat.S_ISREG(child.lstat().st_mode):
            raise RuntimeError(f"unsafe source file: {child}")
        digest = hashlib.sha256()
        with child.open("rb") as stream:
            for block in iter(lambda: stream.read(1 << 20), b""):
                digest.update(block)
        size = child.stat().st_size
        rows.append({
            "path": child.relative_to(root).as_posix(),
            "size": size,
            "sha256": digest.hexdigest(),
        })
        total += size
rows.sort(key=lambda row: row["path"])
canonical = json.dumps(
    rows,
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=False,
    allow_nan=False,
).encode()
print(json.dumps({
    "files": len(rows),
    "bytes": total,
    "tree_sha256": hashlib.sha256(canonical).hexdigest(),
}, sort_keys=True))
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


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_arg(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise argparse.ArgumentTypeError("expected 64 lowercase hexadecimal characters")
    return value


def git_sha_arg(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise argparse.ArgumentTypeError("expected 40 lowercase hexadecimal characters")
    return value


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


def remote_file_sha256(path: Path) -> str:
    return kube("sha256sum", str(path)).decode().split()[0]


def remote_source_identity(root: Path) -> dict[str, Any]:
    return json.loads(kube("python3", "-c", REMOTE_SOURCE_IDENTITY_PROGRAM, str(root)))


def atomic_remote_write(path: Path, payload: bytes) -> dict[str, Any]:
    if not payload:
        raise ValueError("refusing to publish an empty release payload")
    expected_sha256 = sha256_bytes(payload)
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
    expected = {"bytes": len(payload), "sha256": expected_sha256}
    if published != expected:
        raise RuntimeError(f"remote publication acknowledgement differs: {published!r}")
    return published


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--publish", action="store_true", required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--executor-git-sha", type=git_sha_arg, required=True)
    parser.add_argument("--source-files", type=int, required=True)
    parser.add_argument("--source-bytes", type=int, required=True)
    parser.add_argument("--source-tree-sha256", type=sha256_arg, required=True)
    parser.add_argument("--runner-sha256", type=sha256_arg, required=True)
    parser.add_argument("--plan-schema", required=True)
    parser.add_argument("--plan-body-sha256", type=sha256_arg, required=True)
    parser.add_argument("--scientific-label", required=True)
    parser.add_argument("--source-step", type=int, default=EXPECTED_SOURCE_STEP)
    parser.add_argument("--final-step", type=int, default=EXPECTED_FINAL_STEP)
    parser.add_argument(
        "--optimizer-updates", type=int, default=EXPECTED_OPTIMIZER_UPDATES
    )
    arguments = parser.parse_args()

    source = arguments.source_root
    if (
        not source.is_absolute()
        or source.parent != CAMPAIGN_ROOT
        or source.name != f"source_distill_{arguments.executor_git_sha[:8]}"
    ):
        parser.error("source root is not the exact paired campaign staging path")
    if arguments.source_files <= 0 or arguments.source_bytes <= 0:
        parser.error("source identity counts must be positive")
    if (
        arguments.source_step != EXPECTED_SOURCE_STEP
        or arguments.final_step != EXPECTED_FINAL_STEP
        or arguments.optimizer_updates != EXPECTED_OPTIMIZER_UPDATES
    ):
        parser.error(
            "paired campaign step transition or optimizer update count changed"
        )
    if arguments.optimizer_updates != arguments.final_step - arguments.source_step:
        parser.error("optimizer update count does not match the fixed step transition")
    if not re.fullmatch(r"harness-distill\.[a-z0-9-]+-plan\.v1", arguments.plan_schema):
        parser.error("plan schema is malformed")

    observed_source = remote_source_identity(source)
    expected_source = {
        "files": arguments.source_files,
        "bytes": arguments.source_bytes,
        "tree_sha256": arguments.source_tree_sha256,
    }
    if observed_source != expected_source:
        parser.error(
            f"staged source identity differs: {observed_source!r} != {expected_source!r}"
        )
    runner = source / RUNNER_RELATIVE
    if remote_file_sha256(runner) != arguments.runner_sha256:
        parser.error("paired trainer entrypoint SHA-256 changed")

    plan_bytes = remote_bytes(PLAN)
    plan = json.loads(plan_bytes)
    plan_body = {key: item for key, item in plan.items() if key != "plan_body_sha256"}
    if (
        plan.get("schema") != arguments.plan_schema
        or plan.get("status") != "prepared"
        or plan.get("scientific_label") != arguments.scientific_label
        or plan.get("artifact_git_sha") != arguments.executor_git_sha
        or plan.get("source_step") != arguments.source_step
        or plan.get("final_step") != arguments.final_step
        or plan.get("optimizer_updates") != arguments.optimizer_updates
        or plan.get("plan_body_sha256") != arguments.plan_body_sha256
        or digest(plan_body) != arguments.plan_body_sha256
    ):
        parser.error("paired training plan or its sealed identity changed")

    body = {
        "schema": RELEASE_SCHEMA,
        "status": "released",
        "purpose": PURPOSE,
        "laptop_r01_outcomes_read": False,
        "office_chair_outcomes_read": False,
        "reservation": {"job_name": JOB, "pod_name": POD, "pod_uid": POD_UID},
        "source": {
            "root": str(source),
            "git_sha": arguments.executor_git_sha,
            **expected_source,
        },
        "entrypoint": {"path": str(runner), "sha256": arguments.runner_sha256},
        "argv": [
            str(PLAN),
            str(source),
            arguments.executor_git_sha,
            arguments.source_tree_sha256,
            arguments.runner_sha256,
            PRIME_ROOT,
        ],
    }
    release = {**body, "release_sha256": digest(body)}
    payload = json.dumps(release, sort_keys=True, indent=2).encode() + b"\n"
    acknowledgement = atomic_remote_write(RELEASE, payload)
    print(
        json.dumps(
            {
                "status": "published_atomic_create_only",
                "release_path": str(RELEASE),
                "release_file_sha256": acknowledgement["sha256"],
                "release_body_sha256": release["release_sha256"],
                "source": expected_source,
                "plan_file_sha256": sha256_bytes(plan_bytes),
                "plan_body_sha256": arguments.plan_body_sha256,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
