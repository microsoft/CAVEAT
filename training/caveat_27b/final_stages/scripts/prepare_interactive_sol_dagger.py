#!/usr/bin/env python3
"""Validate executed interactive traces and seal the exact campaign-2 corpus."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness_distill.interactive_sol_dagger_data import (
    audit_collection_manifest,
    materialize_exact_collection,
    materialize_heldout_preflight,
    validate_bundle,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate")
    validate.add_argument("--bundle", type=Path, required=True)
    validate.add_argument("--output-root", type=Path, required=True)

    select = commands.add_parser("select")
    select.add_argument("--validation-report", type=Path, required=True)
    select.add_argument("--step25-train-sft", type=Path, required=True)
    select.add_argument("--step25-label-audits", type=Path, required=True)
    select.add_argument("--output-root", type=Path, required=True)

    audit = commands.add_parser("audit-collection")
    audit.add_argument("--manifest", type=Path, required=True)
    heldout = commands.add_parser("heldout-preflight")
    heldout.add_argument("--validation-report", type=Path, required=True)
    heldout.add_argument("--output-root", type=Path, required=True)
    for command in (validate, select, heldout):
        command.add_argument("--collector-commit", required=True)
        command.add_argument("--validator-commit", required=True)
        command.add_argument("--validator-data-sha256", required=True)
        command.add_argument("--validator-cli-sha256", required=True)
    args = parser.parse_args(argv)

    provenance = {
        "collector_commit": getattr(args, "collector_commit", None),
        "validator_commit": getattr(args, "validator_commit", None),
        "validator_data_sha256": getattr(args, "validator_data_sha256", None),
        "validator_cli_sha256": getattr(args, "validator_cli_sha256", None),
    }

    if args.command == "validate":
        result = validate_bundle(
            bundle_path=args.bundle,
            output_root=args.output_root,
            **provenance,
        )
    elif args.command == "select":
        result = materialize_exact_collection(
            validation_report=args.validation_report,
            step25_train_sft=args.step25_train_sft,
            step25_label_audits=args.step25_label_audits,
            output_root=args.output_root,
            **provenance,
        )
    elif args.command == "audit-collection":
        result = audit_collection_manifest(args.manifest)
    else:
        result = materialize_heldout_preflight(
            validation_report=args.validation_report,
            output_root=args.output_root,
            **provenance,
        )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
