#!/usr/bin/env python3
"""Create-once identity seal for a stable Step35 development microcheckpoint."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

SCHEMA = "harness-posttrain.step35-microcheckpoint-inventory.v1"
PLAN_SCHEMA = "harness-distill.step35-trajectory-action-proximal-plan.v1"
HEX64 = re.compile(r"[0-9a-f]{64}")
ANSI = re.compile(r"\x1b\[[0-9;]*m")
EXPECTED_STEPS = {"A": (33, 34, 35), "B": (33, 34)}


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
        raise RuntimeError("microcheckpoint adapter directory is absent or unsafe")
    entries: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise RuntimeError(f"adapter tree contains a symlink: {path}")
        if path.is_file():
            size = path.stat().st_size
            entries[path.relative_to(root).as_posix()] = {
                "size": size,
                "sha256": file_sha256(path),
            }
        elif not path.is_dir():
            raise RuntimeError(f"adapter tree contains a special entry: {path}")
    if not entries:
        raise RuntimeError("microcheckpoint adapter tree is empty")
    return {
        "path": str(root.resolve()),
        "files": len(entries),
        "bytes": sum(int(value["size"]) for value in entries.values()),
        "tree_sha256": hashlib.sha256(canonical(entries)).hexdigest(),
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--plan-file-sha256", required=True)
    parser.add_argument("--plan-body-sha256", required=True)
    parser.add_argument("--profile", choices=sorted(EXPECTED_STEPS), required=True)
    parser.add_argument("--step", type=int, required=True)
    parser.add_argument("--training-output", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()

    if arguments.step not in EXPECTED_STEPS[arguments.profile]:
        parser.error("step is not part of the selected profile")
    for value, label in (
        (arguments.plan_file_sha256, "plan file SHA-256"),
        (arguments.plan_body_sha256, "plan body SHA-256"),
    ):
        if HEX64.fullmatch(value) is None:
            parser.error(f"{label} is malformed")
    plan_path = arguments.plan.resolve()
    training_output = arguments.training_output.resolve()
    output = arguments.output.resolve()
    if (
        not plan_path.is_file()
        or plan_path.is_symlink()
        or file_sha256(plan_path) != arguments.plan_file_sha256
    ):
        parser.error("sealed Step35 plan file changed")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if (
        not isinstance(plan, dict)
        or plan.get("schema") != PLAN_SCHEMA
        or canonical_body(plan, "plan_body_sha256", "plan")
        != arguments.plan_body_sha256
        or plan.get("profile") != arguments.profile
        or Path(str(plan.get("training_output", ""))).resolve() != training_output
        or [item.get("step") for item in plan.get("candidate_checkpoints", [])]
        != list(EXPECTED_STEPS[arguments.profile])
    ):
        parser.error("Step35 plan/profile candidate contract changed")
    expected_path = training_output / f"weights/step_{arguments.step}/lora_adapters"
    checkpoint = next(
        item
        for item in plan["candidate_checkpoints"]
        if item.get("step") == arguments.step
    )
    if (
        Path(str(checkpoint.get("path", ""))).resolve() != expected_path
        or checkpoint.get("selection_status") != "pending_shared_train_only_probe2"
    ):
        parser.error("Step35 plan microcheckpoint entry changed")

    stable = expected_path.parent / "STABLE"
    config = expected_path / "adapter_config.json"
    if (
        not stable.is_file()
        or stable.is_symlink()
        or not config.is_file()
        or config.is_symlink()
    ):
        parser.error("Step35 microcheckpoint is not stable")
    candidate = tree_identity(expected_path)
    candidate.update(
        {
            "step": arguments.step,
            "adapter_config_sha256": file_sha256(config),
            "stable_marker_sha256": file_sha256(stable),
        }
    )
    audit = proximal_audit(training_output / "logs/trainer/torchrun", arguments.step)
    body = {
        "schema": SCHEMA,
        "status": "sealed",
        "development_probe_only": True,
        "training_eligible": False,
        "profile": arguments.profile,
        "step": arguments.step,
        "plan_path": str(plan_path),
        "plan_file_sha256": arguments.plan_file_sha256,
        "plan_body_sha256": arguments.plan_body_sha256,
        "candidate": candidate,
        "proximal_audit": audit,
        "selection_status": "pending_shared_train_only_probe2",
        "final_profile_receipt_required_before_broader_evaluation": True,
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
    print(
        json.dumps(
            {
                "path": str(output),
                "file_sha256": hashlib.sha256(payload).hexdigest(),
                "body_sha256": value["inventory_body_sha256"],
                "candidate": candidate,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
