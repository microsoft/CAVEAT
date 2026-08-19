#!/usr/bin/env python3
"""Stage the step-30 PRECOMMIT-ANY candidate-serving source read-only."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any


BASE = Path(
    "/data/runs/t-yuxuanli/t-yuxuanli-hpt-c2-precommit-paired-w1-20260815"
)
TARGET = BASE / "source_eval_precommit_serve_v1"
STAGING = BASE / ".source_eval_precommit_serve_v1.staging"
BUNDLE = BASE / "source_eval_precommit_serve_v1.git.bundle"
BUNDLE_STAGING = BASE / ".source_eval_precommit_serve_v1.git.bundle.staging"
DESCRIPTOR = BASE / "source_eval_precommit_serve_v1.identity.json"
GIT_SHA = "a3ff8048762cafb80cbc0c5b319b5a9857344e75"
PARENT_GIT_SHA = "9debfc40d9a08674961ae0fa2c0f7225157a5066"
PARENT_TREE_SHA256 = (
    "7586f9c0ac1401991f0de4f12b0cdbfb96a9ce6d23633dec78ee88961736adb0"
)
BUNDLE_SHA256 = "234448cccaab21bee8abdefb859345e66a1eeac67795522d953617308d5de259"


def canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def source_identity(root: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    total = 0
    directories = 0
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        base = Path(directory)
        directory_names.sort()
        file_names.sort()
        if base != root:
            directories += 1
        for name in directory_names:
            child = base / name
            if child.is_symlink() or not stat.S_ISDIR(child.lstat().st_mode):
                raise RuntimeError(f"unsafe source directory: {child}")
        for name in file_names:
            child = base / name
            if child.is_symlink() or not stat.S_ISREG(child.lstat().st_mode):
                raise RuntimeError(f"unsafe source file: {child}")
            size = child.stat().st_size
            rows.append(
                {
                    "path": child.relative_to(root).as_posix(),
                    "size": size,
                    "sha256": file_sha(child),
                }
            )
            total += size
    rows.sort(key=lambda row: str(row["path"]))
    return {
        "files": len(rows),
        "directories": directories,
        "bytes": total,
        "tree_sha256": hashlib.sha256(canonical(rows)).hexdigest(),
    }


def seal(root: Path) -> str:
    rows: list[dict[str, str]] = []
    directories: list[Path] = []
    for directory, directory_names, file_names in os.walk(
        root, topdown=True, followlinks=False
    ):
        base = Path(directory)
        directory_names.sort()
        file_names.sort()
        if base != root:
            directories.append(base)
            rows.append(
                {
                    "path": base.relative_to(root).as_posix(),
                    "kind": "directory",
                    "mode": "0555",
                }
            )
        for name in file_names:
            child = base / name
            mode = 0o555 if stat.S_IMODE(child.lstat().st_mode) & 0o111 else 0o444
            os.chmod(child, mode)
            rows.append(
                {
                    "path": child.relative_to(root).as_posix(),
                    "kind": "file",
                    "mode": format(mode, "04o"),
                }
            )
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        os.chmod(directory, 0o555)
    os.chmod(root, 0o555)
    rows.sort(key=lambda row: (row["path"], row["kind"]))
    return hashlib.sha256(canonical(rows)).hexdigest()


def write_new(path: Path, payload: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o444,
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--uploaded-bundle", type=Path, required=True)
    arguments = parser.parse_args()
    uploaded = arguments.uploaded_bundle.resolve()
    if not uploaded.is_file() or uploaded.is_symlink():
        parser.error("uploaded bundle is not a safe regular file")
    if file_sha(uploaded) != BUNDLE_SHA256:
        parser.error("uploaded PRECOMMIT serving bundle identity changed")
    if any(
        path.exists() or path.is_symlink()
        for path in (TARGET, STAGING, BUNDLE, BUNDLE_STAGING, DESCRIPTOR)
    ):
        parser.error("PRECOMMIT serving staging target already exists")

    subprocess.run(["git", "clone", "-q", str(uploaded), str(STAGING)], check=True)
    subprocess.run(
        ["git", "-C", str(STAGING), "checkout", "-q", "--detach", GIT_SHA],
        check=True,
    )
    observed_git = subprocess.check_output(
        ["git", "-C", str(STAGING), "rev-parse", "HEAD"], text=True
    ).strip()
    if observed_git != GIT_SHA:
        raise RuntimeError("PRECOMMIT serving bundle HEAD changed")
    required = {
        "scripts/run_paired_buy_now_candidate_serve.sh": STAGING
        / "scripts/run_paired_buy_now_candidate_serve.sh",
        "src/harness_posttrain_eval/paired_buy_now_candidate_serve.py": STAGING
        / "src/harness_posttrain_eval/paired_buy_now_candidate_serve.py",
        "tests/test_paired_buy_now_candidate_serve.py": STAGING
        / "tests/test_paired_buy_now_candidate_serve.py",
    }
    if any(not path.is_file() or path.is_symlink() for path in required.values()):
        raise RuntimeError("PRECOMMIT serving source is missing a required file")
    shutil.rmtree(STAGING / ".git")
    identity = source_identity(STAGING)
    required_sha256 = {name: file_sha(path) for name, path in required.items()}
    mode_tree = seal(STAGING)

    write_new(BUNDLE_STAGING, uploaded.read_bytes())
    if file_sha(BUNDLE_STAGING) != BUNDLE_SHA256:
        raise RuntimeError("staged PRECOMMIT serving source bundle changed")
    os.rename(STAGING, TARGET)
    os.rename(BUNDLE_STAGING, BUNDLE)
    body = {
        "schema": "harness-posttrain-ops.campaign2-derived-staged-source.v1",
        "status": "staged_read_only",
        "root": str(TARGET),
        "git_sha": GIT_SHA,
        "derived_from_git_sha": PARENT_GIT_SHA,
        "derived_from_tree_sha256": PARENT_TREE_SHA256,
        **identity,
        "mode_tree_sha256": mode_tree,
        "root_mode": "0555",
        "git_bundle": {
            "path": str(BUNDLE),
            "bytes": BUNDLE.stat().st_size,
            "sha256": file_sha(BUNDLE),
        },
        "required_sha256": required_sha256,
    }
    value = {
        **body,
        "descriptor_body_sha256": hashlib.sha256(canonical(body)).hexdigest(),
    }
    write_new(
        DESCRIPTOR, json.dumps(value, sort_keys=True, indent=2).encode() + b"\n"
    )
    directory = os.open(BASE, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    print(json.dumps(value, sort_keys=True))


if __name__ == "__main__":
    main()
