"""Install an owner-private, mechanically audited cap-64 B200 helper."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .artifacts import (
    ArtifactError,
    publish_bytes,
    publish_json,
    read_json,
    sha256_bytes,
    sha256_file,
)

AUDITED_HELPER_SHA256 = "fc1447730f2b55d0262dbb391cb7cb045ac758e914e951d980f1dc2427468b69"
CAP_LINE_OLD = b"HARD_GPU_CAP=16\n"
CAP_LINE_NEW = b"HARD_GPU_CAP=64\n"
HELP_OLD = b"hard cap of 16"
HELP_NEW = b"hard cap of 64"
CAP64_SCHEMA = "harness-posttrain.cap64-helper.v1"


def default_destination() -> Path:
    return (Path.home() / ".local/lib/harness-posttrain-cap64/b200").resolve()


def _transform(source_bytes: bytes) -> bytes:
    if sha256_bytes(source_bytes) != AUDITED_HELPER_SHA256:
        raise ArtifactError(
            "shared B200 helper hash drifted; cap-64 transformation requires review"
        )
    if source_bytes.count(CAP_LINE_OLD) != 1 or source_bytes.count(HELP_OLD) != 1:
        raise ArtifactError("shared B200 helper no longer has the two audited cap-16 anchors")
    transformed = source_bytes.replace(CAP_LINE_OLD, CAP_LINE_NEW).replace(HELP_OLD, HELP_NEW)
    if transformed.count(CAP_LINE_NEW) != 1 or transformed.count(HELP_NEW) != 1:
        raise ArtifactError("cap-64 transformation was not unique")
    reversed_bytes = transformed.replace(CAP_LINE_NEW, CAP_LINE_OLD).replace(
        HELP_NEW, HELP_OLD
    )
    if reversed_bytes != source_bytes:
        raise ArtifactError(
            "cap-64 reverse transformation does not reproduce the audited helper"
        )
    safety_anchors = (
        (b"active_gpu_total()", 1),
        (b"acquire_submission_lock()", 1),
        (b"release_submission_lock()", 1),
        (b"guard_gpu_budget \"$requested\"", 1),
        (b"guard_gpu_budget \"$((NODES * GPUS_PER_NODE))\"", 1),
        (b"-l \"submitter=$USER_ALIAS\"", 2),
    )
    for anchor, expected_count in safety_anchors:
        if (
            source_bytes.count(anchor) != expected_count
            or transformed.count(anchor) != expected_count
        ):
            raise ArtifactError(f"allocation safety anchor drifted: {anchor!r}")
    return transformed


def install_cap64_helper(
    *,
    source: str | Path,
    destination: str | Path,
) -> dict[str, Any]:
    source_path = Path(source).resolve()
    destination_path = Path(destination).resolve()
    if destination_path.name != "b200" or destination_path == Path.home().resolve():
        raise ArtifactError("cap-64 destination must be an explicit file named b200")
    transformed = _transform(source_path.read_bytes())
    installed = publish_bytes(destination_path, transformed)
    installed.chmod(0o700)
    manifest = {
        "schema": CAP64_SCHEMA,
        "source": str(source_path),
        "source_sha256": sha256_file(source_path),
        "installed": str(installed),
        "installed_sha256": sha256_file(installed),
        "hard_gpu_cap": 64,
        "mechanical_changes": [
            "HARD_GPU_CAP=16 -> HARD_GPU_CAP=64",
            "help text: hard cap of 16 -> hard cap of 64",
        ],
        "reverse_transform_equals_source": True,
        "atomic_submission_lock_preserved": True,
        "all_nonterminal_submitter_jobs_counted": True,
    }
    publish_json(installed.with_name("manifest.json"), manifest)
    return manifest


def check_cap64_helper(
    *,
    source: str | Path,
    destination: str | Path,
) -> dict[str, Any]:
    source_path = Path(source).resolve()
    destination_path = Path(destination).resolve()
    expected = _transform(source_path.read_bytes())
    if not destination_path.is_file() or destination_path.read_bytes() != expected:
        raise ArtifactError("owner-private cap-64 helper is absent or drifted")
    manifest = read_json(destination_path.with_name("manifest.json"))
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema") != CAP64_SCHEMA
        or manifest.get("source_sha256") != sha256_file(source_path)
        or manifest.get("installed_sha256") != sha256_file(destination_path)
    ):
        raise ArtifactError("owner-private cap-64 manifest is absent or drifted")
    mode = destination_path.stat().st_mode & 0o777
    if mode != 0o700:
        raise ArtifactError(f"owner-private cap-64 helper mode must be 0700, got {mode:o}")
    return manifest


def helper_environment() -> dict[str, str]:
    value = os.environ.get("B200_MAX_GPUS", "64")
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ArtifactError("B200_MAX_GPUS must be an integer") from exc
    if not 0 <= parsed <= 64:
        raise ArtifactError(
            "B200_MAX_GPUS may lower, but never exceed, the campaign cap of 64"
        )
    return {"B200_MAX_GPUS": str(parsed)}
