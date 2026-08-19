#!/usr/bin/env python3
"""Create-once PVC staging for the sealed Step32 TRAIN restore server."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any

ARCHIVE = Path("/tmp/source.tar.gz")
TARGET = Path(
    "/data/runs/t-yuxuanli/"
    "t-yuxuanli-hpt-c2-step32-rebind-training-w1-20260815/"
    "source_eval_step32_parent_restore_train_v1"
)
STAGING = TARGET.with_name(f".{TARGET.name}.staging")
DESCRIPTOR = TARGET.with_name(f"{TARGET.name}.identity.json")
ARCHIVE_SHA256 = "d57bd3d676f589ff9013e60c2f560463404cfefa93f1a5d6fb5a3a6d5869f0bc"
ARCHIVE_BYTES = 351_821
GIT_SHA = "370684080e4966a8aed882768ba5327aa33fb275"
FILES = 92
DIRECTORIES = 7
BYTES = 2_184_320
TREE_SHA256 = "fcb004663452955c409a5d68aa5fe6672f9603f3377b45cdc676c94594ababfe"
MODE_TREE_SHA256 = "dff9db6365db297e2ac476dd726a7771a7c850de418789892a79debd2b0bd0e3"
REQUIRED = {
    "scripts/run_paired_buy_now_candidate_serve.sh": (
        "1d99ad0292a59a165d2211cf0ee126f8794b703ad76c7f7f5fd3dba6efdffc84"
    ),
}


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


def identity(root: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    modes: list[dict[str, str]] = []
    directories: list[Path] = []
    total = 0
    for directory, directory_names, file_names in os.walk(
        root, topdown=True, followlinks=False
    ):
        base = Path(directory)
        directory_names.sort()
        file_names.sort()
        if base != root:
            directories.append(base)
            modes.append(
                {
                    "path": base.relative_to(root).as_posix(),
                    "kind": "directory",
                    "mode": "0555",
                }
            )
        for name in directory_names:
            child = base / name
            if child.is_symlink() or not stat.S_ISDIR(child.lstat().st_mode):
                raise RuntimeError(f"unsafe source directory: {child}")
        for name in file_names:
            child = base / name
            if child.is_symlink() or not stat.S_ISREG(child.lstat().st_mode):
                raise RuntimeError(f"unsafe source file: {child}")
            size = child.stat().st_size
            total += size
            rows.append(
                {
                    "path": child.relative_to(root).as_posix(),
                    "size": size,
                    "sha256": file_sha(child),
                }
            )
            mode = 0o555 if stat.S_IMODE(child.lstat().st_mode) & 0o111 else 0o444
            modes.append(
                {
                    "path": child.relative_to(root).as_posix(),
                    "kind": "file",
                    "mode": format(mode, "04o"),
                }
            )
    rows.sort(key=lambda row: row["path"])
    modes.sort(key=lambda row: (row["path"], row["kind"]))
    return {
        "files": len(rows),
        "directories": len(directories),
        "bytes": total,
        "tree_sha256": hashlib.sha256(canonical(rows)).hexdigest(),
        "mode_tree_sha256": hashlib.sha256(canonical(modes)).hexdigest(),
        "directory_paths": directories,
        "file_rows": rows,
    }


def main() -> None:
    if file_sha(ARCHIVE) != ARCHIVE_SHA256 or ARCHIVE.stat().st_size != ARCHIVE_BYTES:
        raise RuntimeError("source archive identity changed")
    if any(path.exists() or path.is_symlink() for path in (TARGET, STAGING, DESCRIPTOR)):
        raise RuntimeError("source target/staging/descriptor already exists")
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    STAGING.mkdir(mode=0o700)
    with tarfile.open(ARCHIVE, "r:gz") as stream:
        seen: set[str] = set()
        for member in stream.getmembers():
            path = PurePosixPath(member.name)
            if (
                not member.name
                or "\\" in member.name
                or path.is_absolute()
                or ".." in path.parts
                or member.name in seen
                or not (member.isfile() or member.isdir())
            ):
                raise RuntimeError(f"unsafe archive member: {member.name}")
            seen.add(member.name)
        stream.extractall(STAGING, filter="data")
    observed = identity(STAGING)
    expected = {
        "files": FILES,
        "directories": DIRECTORIES,
        "bytes": BYTES,
        "tree_sha256": TREE_SHA256,
        "mode_tree_sha256": MODE_TREE_SHA256,
    }
    if {key: observed[key] for key in expected} != expected:
        raise RuntimeError("extracted source identity changed")
    if (STAGING / ".git").exists() or (STAGING / "uv.lock").exists():
        raise RuntimeError("staged source contains forbidden metadata")
    if any(file_sha(STAGING / path) != value for path, value in REQUIRED.items()):
        raise RuntimeError("required restore source file changed")
    for row in observed["file_rows"]:
        path = STAGING / row["path"]
        mode = 0o555 if stat.S_IMODE(path.lstat().st_mode) & 0o111 else 0o444
        os.chmod(path, mode)
    for path in sorted(
        observed["directory_paths"], key=lambda item: len(item.parts), reverse=True
    ):
        os.chmod(path, 0o555)
    os.chmod(STAGING, 0o555)
    os.rename(STAGING, TARGET)
    value = {
        "schema": "harness-posttrain-ops.campaign2-staged-source.v1",
        "status": "staged_read_only",
        "root": str(TARGET),
        "git_sha": GIT_SHA,
        "archive_sha256": ARCHIVE_SHA256,
        "archive_bytes": ARCHIVE_BYTES,
        **expected,
        "root_mode": "0555",
        "required_sha256": REQUIRED,
    }
    payload = json.dumps(value, sort_keys=True, indent=2).encode() + b"\n"
    descriptor = os.open(
        DESCRIPTOR,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o444,
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    print(json.dumps(value, sort_keys=True))


if __name__ == "__main__":
    main()
