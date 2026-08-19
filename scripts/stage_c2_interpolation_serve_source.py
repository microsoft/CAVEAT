#!/usr/bin/env python3
"""Create-once PVC staging for the Step35 microprofile development server."""

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
    "t-yuxuanli-hpt-c2-step35-micro-candidate-w1-20260816/"
    "source_eval_step35_micro_profile_v1"
)
STAGING = TARGET.with_name(f".{TARGET.name}.staging")
DESCRIPTOR = TARGET.with_name(f"{TARGET.name}.identity.json")
ARCHIVE_SHA256 = "4c2dad3342d7ff59349c5f4b417879e94385908c0496eb8808a07dcfc1835fd3"
ARCHIVE_BYTES = 366_536
GIT_SHA = "479d442e348b732f68b4c83fc28e35c97ee84806"
FILES = 95
DIRECTORIES = 7
BYTES = 2_217_182
TREE_SHA256 = "d7932e3f0535dbed8bb558dee613a422001a9809b2e2e70627252f900fa3eac4"
MODE_TREE_SHA256 = "91a6d25226a502b7806acfcaacb376a4531d9a72d05a6a1bef731bf998defebf"
REQUIRED = {
    "scripts/run_step35_micro_profile_candidate_serve.sh": (
        "f9c12e3c7878fd59070c71d4a7812bc16460948e831d0846c0dd26b6eeb0ae9e"
    ),
    "src/harness_posttrain_eval/step35_micro_profile_candidate_serve.py": (
        "e6859973738dbc6efe7014637201a2518cb7eb9bf07942ffde852af9364d3f4b"
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
        raise RuntimeError("required Step35 microprofile source file changed")
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
