#!/usr/bin/env python3
"""Prepare, validate, and receipt the exact8 paired preference update."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness_distill import action_weighted_ce_training as base
from harness_distill.paired_buy_now_training import (
    prepare_paired_buy_now_training,
    validate_exact8_pairs,
    validate_step25_parent_fast,
    validate_training_plan,
    validate_training_receipt,
    write_training_receipt,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    pairs = commands.add_parser("validate-pairs")
    pairs.add_argument("--manifest", type=Path, required=True)
    parent = commands.add_parser("validate-parent")
    parent.add_argument("--receipt", type=Path, required=True)
    prime = commands.add_parser("validate-prime")
    prime.add_argument("--prime-root", type=Path, required=True)
    prime.add_argument("--require-launch-patches", action="store_true")
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--exact8-manifest", type=Path, required=True)
    prepare.add_argument("--parent-receipt", type=Path, required=True)
    prepare.add_argument("--output-dir", type=Path, required=True)
    prepare.add_argument("--prime-root", type=Path, default=Path("/opt/prime-rl"))
    prepare.add_argument("--artifact-git-sha", required=True)
    prepare.add_argument("--topology", choices=("cp2_dp2", "cp4_dp1"), default="cp2_dp2")
    prepare.add_argument("--fallback-reason")
    plan = commands.add_parser("validate-plan")
    plan.add_argument("--plan", type=Path, required=True)
    plan.add_argument("--require-launch-patches", action="store_true")
    write = commands.add_parser("write-receipt")
    write.add_argument("--plan", type=Path, required=True)
    write.add_argument("--executor-git-sha", required=True)
    receipt = commands.add_parser("validate-receipt")
    receipt.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "validate-pairs":
        result = validate_exact8_pairs(args.manifest)
        result = {key: value for key, value in result.items() if key != "pairs"}
    elif args.command == "validate-parent":
        result = validate_step25_parent_fast(args.receipt)
    elif args.command == "validate-prime":
        result = base.validate_prime_source(
            args.prime_root, require_launch_patches=args.require_launch_patches
        )
    elif args.command == "prepare":
        result = prepare_paired_buy_now_training(
            exact8_manifest_path=args.exact8_manifest,
            parent_receipt_path=args.parent_receipt,
            output_dir=args.output_dir,
            prime_root=args.prime_root,
            artifact_git_sha=args.artifact_git_sha,
            topology_name=args.topology,
            fallback_reason=args.fallback_reason,
        )
    elif args.command == "validate-plan":
        result = validate_training_plan(
            args.plan, require_launch_patches=args.require_launch_patches
        )
    elif args.command == "write-receipt":
        result = write_training_receipt(
            plan_path=args.plan, executor_git_sha=args.executor_git_sha
        )
    else:
        result = validate_training_receipt(args.receipt)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
