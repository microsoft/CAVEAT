#!/usr/bin/env python3
"""Stage and verify the sealed Step36 outcome-ranked adapter create-once."""

from __future__ import annotations

import hashlib
import json
import os
import tarfile
from pathlib import Path, PurePosixPath

ARCHIVE = Path("/tmp/c2_step36_outcome_adapter.tar.gz")
ARCHIVE_SHA256 = "7200632c8b1945dcf4617482e1345c5ba7e83cd8fa092ca846e0a410fd251cb1"
ARCHIVE_BYTES = 3_124_343
CAMPAIGN = Path(
    "/data/runs/t-yuxuanli/"
    "t-yuxuanli-hpt-c2-step36-outcome-ranked-training-w1-20260816"
)
TARGET = CAMPAIGN / "verified_outcome_adapter_r2"
STAGING = CAMPAIGN / ".verified_outcome_adapter_r2.staging"
DESCRIPTOR = CAMPAIGN / "verified_outcome_adapter_r2.identity.json"
EXPECTED = {
    "adapter.json": (
        15_109,
        "86557869acc56813d7abcd48d57fb61876a21699935c25ffd619c5f0b8b6a93f",
    ),
    "fresh_hero50_pairs.jsonl": (
        3_315_522,
        "1d02489f66206b0695395bfee0fc320a5a76cfdd98a896e3b03af9ef94ca6f3d",
    ),
    "paired_corpus.jsonl": (
        4_473_865,
        "62984828d5db06d572dde031f6ce4067fb3e24d09d42109df96d5cba0281d299",
    ),
    "shortcut_retention_pairs.jsonl": (
        2_844_696,
        "d568e95dfcf1b4444e93cc6fa01984d810e2ac24c17b87b1c1f6150ac3481444",
    ),
}


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    if _sha(ARCHIVE) != ARCHIVE_SHA256 or ARCHIVE.stat().st_size != ARCHIVE_BYTES:
        raise RuntimeError("adapter archive identity changed")
    if any(path.exists() or path.is_symlink() for path in (TARGET, STAGING, DESCRIPTOR)):
        raise RuntimeError("adapter target/staging/descriptor already exists")
    CAMPAIGN.mkdir(parents=True, exist_ok=True)
    STAGING.mkdir(mode=0o700)
    with tarfile.open(ARCHIVE, "r:gz") as stream:
        members = stream.getmembers()
        seen: set[str] = set()
        for member in members:
            path = PurePosixPath(member.name)
            if (
                not member.name
                or "\\" in member.name
                or path.is_absolute()
                or ".." in path.parts
                or member.name in seen
                or not (member.isfile() or member.isdir())
                or path.parts[0] != "adapter"
            ):
                raise RuntimeError(f"unsafe adapter archive member: {member.name}")
            seen.add(member.name)
        stream.extractall(STAGING, filter="data")
    extracted = STAGING / "adapter"
    observed = {
        path.relative_to(extracted).as_posix(): (path.stat().st_size, _sha(path))
        for path in sorted(extracted.iterdir())
        if path.is_file() and not path.is_symlink()
    }
    if observed != EXPECTED or len(list(extracted.iterdir())) != len(EXPECTED):
        raise RuntimeError("staged adapter identity changed")
    for path in extracted.iterdir():
        os.chmod(path, 0o444)
    os.chmod(extracted, 0o555)
    os.rename(extracted, TARGET)
    os.rmdir(STAGING)
    value = {
        "schema": "harness-posttrain-ops.campaign2-staged-adapter.v1",
        "status": "staged_read_only",
        "root": str(TARGET),
        "archive_sha256": ARCHIVE_SHA256,
        "archive_bytes": ARCHIVE_BYTES,
        "files": len(EXPECTED),
        "bytes": sum(size for size, _ in EXPECTED.values()),
        "file_sha256": {name: digest for name, (_, digest) in EXPECTED.items()},
    }
    payload = json.dumps(value, sort_keys=True, indent=2).encode() + b"\n"
    descriptor = os.open(DESCRIPTOR, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    print(json.dumps(value, sort_keys=True))


if __name__ == "__main__":
    main()
