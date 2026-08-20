#!/usr/bin/env python3
"""Create and validate the standalone Sol-to-Qwen DAgger SFT plan."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness_distill.sol_dagger_training import (
    prepare_sol_dagger_training,
    validate_collection_manifest,
    validate_repair_step24_parent,
    validate_sol_dagger_plan,
    validate_sol_dagger_receipt,
    write_sol_dagger_receipt,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)

    parent = commands.add_parser("validate-parent")
    parent.add_argument("--receipt", type=Path, required=True)

    collection = commands.add_parser("validate-collection")
    collection.add_argument("--manifest", type=Path, required=True)

    prepare = commands.add_parser("prepare")
    prepare.add_argument("--collection-manifest", type=Path, required=True)
    prepare.add_argument("--smoke-report", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--prime-root", type=Path, default=Path("/opt/prime-rl"))

    plan = commands.add_parser("validate-plan")
    plan.add_argument("--plan", type=Path, required=True)

    receipt = commands.add_parser("write-receipt")
    receipt.add_argument("--plan", type=Path, required=True)
    receipt.add_argument("--executor-git-sha", required=True)

    receipt_validation = commands.add_parser("validate-receipt")
    receipt_validation.add_argument("--receipt", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "validate-parent":
        result = validate_repair_step24_parent(args.receipt)
    elif args.command == "validate-collection":
        result = validate_collection_manifest(args.manifest)
    elif args.command == "prepare":
        result = prepare_sol_dagger_training(
            collection_manifest_path=args.collection_manifest,
            smoke_report_path=args.smoke_report,
            output_dir=args.output,
            prime_root=args.prime_root,
        )
    elif args.command == "validate-plan":
        result = validate_sol_dagger_plan(args.plan)
    elif args.command == "validate-receipt":
        result = validate_sol_dagger_receipt(args.receipt)
    else:
        result = write_sol_dagger_receipt(
            plan_path=args.plan, executor_git_sha=args.executor_git_sha
        )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
