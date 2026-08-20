#!/usr/bin/env python3
"""Apply the narrow PRIME-RL v0.7 grouped-MM backward-layout fix."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PATCH = PACKAGE_ROOT / "patches" / "prime_rl_v0.7_grouped_mm_contiguous_grad.patch"
EXPECTED_COMMIT = "d334ea52940b47f426293a7d146239e3fbf91caa"
EXPECTED_PATCH_SHA256 = "642705146bf30890d210d0a3fe27fb2343d128a44b76e3b97a5f3214d3f7e486"
TARGET = "src/prime_rl/trainer/models/layers/lora/multi_linear.py"
EXPECTED_PREHASH = "203e1704c8f0284dad024ff5b4061a40c48cb4411360d672b071e6c79792b4ba"
EXPECTED_POSTHASH = "b109d0efc1753dcea7a132f9cc48ecacdb84b33b0cb346c7b333f9cc1a261767"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("prime_root", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = args.prime_root.resolve()
    errors: list[str] = []

    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, text=True, capture_output=True)
    if commit.returncode != 0 or commit.stdout.strip() != EXPECTED_COMMIT:
        errors.append(
            f"PRIME-RL commit must be {EXPECTED_COMMIT}; found {commit.stdout.strip() or 'unknown'}"
        )

    patch_hash = _sha256(PATCH) if PATCH.is_file() and not PATCH.is_symlink() else "missing"
    if patch_hash != EXPECTED_PATCH_SHA256:
        errors.append(f"patch: expected {EXPECTED_PATCH_SHA256}, found {patch_hash}")

    target = root / TARGET
    found = _sha256(target) if target.is_file() and not target.is_symlink() else "missing"
    if found == EXPECTED_POSTHASH and not errors:
        print(
            json.dumps(
                {
                    "status": "ok",
                    "mode": "already_applied",
                    "prime_commit": EXPECTED_COMMIT,
                    "patch_sha256": patch_hash,
                    "target": TARGET,
                    "target_sha256": found,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if found != EXPECTED_PREHASH:
        errors.append(f"{TARGET}: expected {EXPECTED_PREHASH}, found {found}")

    check = subprocess.run(
        ["git", "apply", "--check", str(PATCH)], cwd=root, text=True, capture_output=True
    )
    if check.returncode != 0:
        errors.append(f"patch does not apply cleanly: {check.stderr.strip()}")
    if errors:
        print(json.dumps({"status": "error", "errors": errors}, indent=2))
        return 2

    mode = "check"
    if not args.check:
        subprocess.run(["git", "apply", str(PATCH)], cwd=root, check=True)
        found = _sha256(target)
        if found != EXPECTED_POSTHASH:
            print(
                json.dumps(
                    {
                        "status": "error",
                        "errors": [
                            f"{TARGET}: expected post-patch {EXPECTED_POSTHASH}, found {found}"
                        ],
                    },
                    indent=2,
                )
            )
            return 2
        mode = "applied"

    print(
        json.dumps(
            {
                "status": "ok",
                "mode": mode,
                "prime_commit": EXPECTED_COMMIT,
                "patch_sha256": patch_hash,
                "target": TARGET,
                "target_sha256": found,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
