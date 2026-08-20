#!/usr/bin/env python3
"""Apply the narrow PRIME-RL v0.7 cross-stage RL resume fix."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PATCH = PACKAGE_ROOT / "patches" / "prime_rl_v0.7_rl_cross_stage_resume.patch"
EXPECTED_COMMIT = "d334ea52940b47f426293a7d146239e3fbf91caa"
TARGET = "src/prime_rl/trainer/rl/train.py"
EXPECTED_PREHASH = "b4859d0133af2eec8809ac90e84d2a3cd58a49f6d388b646d1d8e7e25e3ff41c"
EXPECTED_POSTHASH = "0fe25f3434b144f04b320f65b5477946d0f4f01d0e294837c9b593e7269a6ede"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("prime_root", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = args.prime_root.resolve()
    errors: list[str] = []

    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True, capture_output=True
    )
    if commit.returncode != 0 or commit.stdout.strip() != EXPECTED_COMMIT:
        errors.append(
            f"PRIME-RL commit must be {EXPECTED_COMMIT}; "
            f"found {commit.stdout.strip() or 'unknown'}"
        )

    target = root / TARGET
    found = _sha256(target) if target.is_file() else "missing"
    if found == EXPECTED_POSTHASH:
        print(
            json.dumps(
                {
                    "status": "ok",
                    "mode": "already_applied",
                    "prime_commit": EXPECTED_COMMIT,
                    "patch_sha256": _sha256(PATCH),
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
                "patch_sha256": _sha256(PATCH),
                "target_sha256": found,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
