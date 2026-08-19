#!/usr/bin/env python3
"""Create-once seal for one stable Step36 outcome-ranked microcheckpoint."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

PROFILE = "A"
STEPS = (33, 34, 35)
PLAN_SCHEMA = "harness-distill.step35-trajectory-action-proximal-plan.v1"
MICRO_INVENTORY_SCHEMA = "harness-posttrain.step35-microcheckpoint-inventory.v1"
SELECTION_STATUS = "pending_shared_train_only_probe2"
CAMPAIGN_ROOT = Path(
    "/data/runs/t-yuxuanli/"
    "t-yuxuanli-hpt-c2-step36-outcome-ranked-training-w1-20260816"
)
RUN_ROOT = CAMPAIGN_ROOT / "step36_training_run_r1"
PLAN = RUN_ROOT / "training/plan.json"
PLAN_FILE_SHA256 = "aa7f0b55596df5211c960cc492416ba33d80f74cd1f77f375601a656affb49f2"
PLAN_BODY_SHA256 = "38d996c246298872be208a6a1ab8e2093395cefe33f46979ccb11036216c1176"
TRAINING_OUTPUT = RUN_ROOT / "training/prime_output"
INVENTORY_ROOT = PLAN.parent / "microcheckpoint_inventories"
TRAINER_SOURCE = CAMPAIGN_ROOT / "source_distill_57688c01"
TRAINER_GIT_SHA = "57688c012f77a07d7cfeaea938e08d1b07d66342"
TRAINER_TREE_SHA256 = "8133bdb472456c73b2f8b1633d219f975551bd6d21f3b1fe94ee83caaf37fde8"
TRAINER_FILES = 372
TRAINER_BYTES = 46_678_220
TRAINER_RUNNER = TRAINER_SOURCE / "scripts/run_step35_trajectory_training.sh"
TRAINER_RUNNER_SHA256 = "01be3138e995908c203ccf8616507a2f8b1e0b996f12f099ff5d5f8afa6a9858"
HEX64 = re.compile(r"[0-9a-f]{64}")
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_identity(root: Path) -> dict[str, Any]:
    if not root.is_dir() or root.is_symlink():
        raise RuntimeError("identity tree is absent or unsafe")
    entries: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise RuntimeError(f"identity tree contains a symlink: {path}")
        if path.is_file() and stat.S_ISREG(path.lstat().st_mode):
            size = path.stat().st_size
            entries[path.relative_to(root).as_posix()] = {
                "size": size,
                "sha256": file_sha256(path),
            }
        elif not path.is_dir():
            raise RuntimeError(f"identity tree contains a special entry: {path}")
    if not entries:
        raise RuntimeError("identity tree is empty")
    return {
        "path": str(root.resolve()),
        "files": len(entries),
        "bytes": sum(int(value["size"]) for value in entries.values()),
        "tree_sha256": hashlib.sha256(canonical(entries)).hexdigest(),
    }


def source_identity(root: Path) -> dict[str, Any]:
    """Match the canonical sorted-list identity used by staged source trees."""

    if not root.is_dir() or root.is_symlink():
        raise RuntimeError("trainer source tree is absent or unsafe")
    rows: list[dict[str, Any]] = []
    for directory, directory_names, file_names in os.walk(
        root, topdown=True, followlinks=False
    ):
        base = Path(directory)
        directory_names.sort()
        file_names.sort()
        for name in directory_names:
            child = base / name
            if child.is_symlink() or not stat.S_ISDIR(child.lstat().st_mode):
                raise RuntimeError(f"unsafe trainer source directory: {child}")
        for name in file_names:
            child = base / name
            if child.is_symlink() or not stat.S_ISREG(child.lstat().st_mode):
                raise RuntimeError(f"unsafe trainer source file: {child}")
            rows.append(
                {
                    "path": child.relative_to(root).as_posix(),
                    "size": child.stat().st_size,
                    "sha256": file_sha256(child),
                }
            )
    rows.sort(key=lambda row: row["path"])
    if not rows:
        raise RuntimeError("trainer source tree is empty")
    return {
        "path": str(root.resolve()),
        "files": len(rows),
        "bytes": sum(int(row["size"]) for row in rows),
        "tree_sha256": hashlib.sha256(canonical(rows)).hexdigest(),
    }


def canonical_body(value: dict[str, Any], field: str, label: str) -> str:
    claimed = value.get(field)
    if not isinstance(claimed, str) or HEX64.fullmatch(claimed) is None:
        raise RuntimeError(f"{label} body SHA-256 is malformed")
    body = {key: item for key, item in value.items() if key != field}
    if hashlib.sha256(canonical(body)).hexdigest() != claimed:
        raise RuntimeError(f"{label} canonical body changed")
    return claimed


def proximal_audit(log_root: Path, step: int) -> dict[str, str]:
    if not log_root.is_dir() or log_root.is_symlink():
        raise RuntimeError("trainer log root is absent or unsafe")
    needle = f"STEP35_PROXIMAL_AUDIT step={step} "
    matches: set[str] = set()
    for path in sorted(log_root.rglob("*")):
        if path.is_symlink():
            raise RuntimeError(f"trainer log tree contains a symlink: {path}")
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = ANSI.sub("", raw)
            offset = line.find(needle)
            if offset >= 0:
                marker = line[offset:].strip()
                if not marker.endswith(" status=ok"):
                    raise RuntimeError("proximal audit marker is not successful")
                matches.add(marker)
    if len(matches) != 1:
        raise RuntimeError("expected one unique successful proximal audit marker")
    marker = matches.pop()
    return {
        "marker": marker,
        "marker_sha256": hashlib.sha256(marker.encode()).hexdigest(),
    }


def require_step(step: int) -> int:
    if type(step) is not int or step not in STEPS:
        raise ValueError("Step36 candidate step must be exactly 33, 34, or 35")
    return step


def inventory_path(step: int) -> Path:
    require_step(step)
    return INVENTORY_ROOT / f"step_{step}.json"


def candidate_path(step: int) -> Path:
    require_step(step)
    return TRAINING_OUTPUT / f"weights/step_{step}/lora_adapters"


def _validated_plan() -> dict[str, Any]:
    path = PLAN
    if (
        path.is_symlink()
        or not path.is_file()
        or file_sha256(path) != PLAN_FILE_SHA256
    ):
        raise RuntimeError("sealed Step36 plan file changed")
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict)
        or value.get("schema") != PLAN_SCHEMA
        or canonical_body(value, "plan_body_sha256", "plan") != PLAN_BODY_SHA256
        or value.get("status") != "prepared"
        or value.get("profile") != PROFILE
        or value.get("artifact_git_sha") != TRAINER_GIT_SHA
        or value.get("source_step") != 32
        or value.get("update_steps") != list(STEPS)
        or value.get("final_step") != STEPS[-1]
        or value.get("optimizer_updates") != len(STEPS)
        or Path(str(value.get("training_output", ""))).resolve()
        != TRAINING_OUTPUT
    ):
        raise RuntimeError("Step36 plan/profile/trainer contract changed")
    inventory = value.get("candidate_checkpoints")
    if (
        not isinstance(inventory, list)
        or [item.get("step") if isinstance(item, dict) else None for item in inventory]
        != list(STEPS)
    ):
        raise RuntimeError("Step36 plan checkpoint inventory changed")
    for step, item in zip(STEPS, inventory, strict=True):
        if (
            item.get("selection_status") != SELECTION_STATUS
            or Path(str(item.get("path", ""))).resolve()
            != candidate_path(step)
        ):
            raise RuntimeError("Step36 plan checkpoint binding changed")
    return value


def _validate_trainer_source() -> dict[str, Any]:
    source = TRAINER_SOURCE
    if source.is_symlink() or not source.is_dir() or (source / ".git").exists():
        raise RuntimeError("Step36 trainer source is not an inert staged tree")
    observed = source_identity(source)
    expected = {
        "path": str(source.resolve()),
        "files": TRAINER_FILES,
        "bytes": TRAINER_BYTES,
        "tree_sha256": TRAINER_TREE_SHA256,
    }
    runner = TRAINER_RUNNER
    if (
        observed != expected
        or runner.is_symlink()
        or not runner.is_file()
        or file_sha256(runner) != TRAINER_RUNNER_SHA256
    ):
        raise RuntimeError("Step36 staged trainer source identity changed")
    return {
        "root": str(source.resolve()),
        "git_sha": TRAINER_GIT_SHA,
        **observed,
        "runner": str(runner.resolve()),
        "runner_sha256": TRAINER_RUNNER_SHA256,
    }


def seal(step: int, output: Path) -> dict[str, Any]:
    require_step(step)
    expected_output = inventory_path(step)
    if output.resolve() != expected_output:
        raise RuntimeError(f"inventory output must be exactly {expected_output}")
    _validated_plan()
    trainer = _validate_trainer_source()

    checkpoint = candidate_path(step)
    stable = checkpoint.parent / "STABLE"
    config = checkpoint / "adapter_config.json"
    if (
        stable.is_symlink()
        or not stable.is_file()
        or config.is_symlink()
        or not config.is_file()
    ):
        raise RuntimeError("Step36 microcheckpoint is not STABLE")
    candidate = tree_identity(checkpoint)
    candidate.update(
        {
            "step": step,
            "adapter_config_sha256": file_sha256(config),
            "stable_marker_sha256": file_sha256(stable),
        }
    )
    audit = proximal_audit(TRAINING_OUTPUT / "logs/trainer/torchrun", step)
    body = {
        "schema": MICRO_INVENTORY_SCHEMA,
        "status": "sealed",
        "development_probe_only": True,
        "training_eligible": False,
        "profile": PROFILE,
        "step": step,
        "plan_path": str(PLAN),
        "plan_file_sha256": PLAN_FILE_SHA256,
        "plan_body_sha256": PLAN_BODY_SHA256,
        "candidate": candidate,
        "proximal_audit": audit,
        "selection_status": SELECTION_STATUS,
        "final_profile_receipt_required_before_broader_evaluation": True,
        "step36_trainer_source": trainer,
    }
    value = {
        **body,
        "inventory_body_sha256": hashlib.sha256(canonical(body)).hexdigest(),
    }
    payload = json.dumps(value, sort_keys=True, indent=2).encode() + b"\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        output,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o444,
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    return {
        "path": str(output),
        "file_sha256": hashlib.sha256(payload).hexdigest(),
        "body_sha256": value["inventory_body_sha256"],
        "candidate": candidate,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", type=int, choices=STEPS, required=True)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    output = arguments.output or inventory_path(arguments.step)
    try:
        value = seal(arguments.step, output.resolve())
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(value, sort_keys=True))


if __name__ == "__main__":
    main()
