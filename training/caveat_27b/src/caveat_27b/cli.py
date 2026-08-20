"""Receipt-gated commands used by the CAVEAT-27B parent and fixed-v7 stages."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from .browser_action_fixed_v7 import (
    prepare_browser_action_fixed_v7,
    write_browser_action_fixed_v7_receipt,
)
from .config import Campaign
from .exact_lora import finalize_exact_lora
from .finalize import finalize_refinement
from .post_sft import prepare_refinement
from .prime_data import materialize_prime_dataset
from .procedural_corpus import build_procedural_corpora
from .receipts import write_candidate_receipt, write_prep_receipt, write_refinement_receipt
from .train_configs import generate_sft_configs


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="caveat-27b")
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate-config")
    validate.add_argument("--campaign", type=Path, required=True)

    procedural = commands.add_parser("build-procedural-corpora")
    procedural.add_argument("--campaign", type=Path, required=True)
    generator = procedural.add_mutually_exclusive_group(required=True)
    generator.add_argument("--generator-root", type=Path)
    generator.add_argument("--generator-wheel", type=Path)
    procedural.add_argument("--task-bank", type=Path)
    procedural.add_argument("--output", type=Path, required=True)

    prime = commands.add_parser("materialize-prime")
    prime.add_argument("--campaign", type=Path, required=True)
    prime.add_argument("--source-jsonl", type=Path, required=True)
    prime.add_argument("--source-manifest", type=Path, required=True)
    prime.add_argument("--smoke-report", type=Path, required=True)
    prime.add_argument("--stage", choices=("targeted_sft", "refinement"), required=True)
    prime.add_argument("--candidate", choices=("balanced", "protocol-heavy", "recovery-heavy"))
    prime.add_argument("--output", type=Path, required=True)
    prime.add_argument("--prime-root", type=Path)

    configs = commands.add_parser("generate-sft-configs")
    configs.add_argument("--campaign", type=Path, required=True)
    configs.add_argument("--stage", choices=("targeted_sft", "refinement"), required=True)
    configs.add_argument("--candidate", choices=("balanced", "protocol-heavy", "recovery-heavy"))
    configs.add_argument("--smoke-report", type=Path, required=True)
    configs.add_argument("--parent-model", type=Path, required=True)
    configs.add_argument("--dataset", type=Path, required=True)
    configs.add_argument("--output", type=Path, required=True)
    configs.add_argument("--num-gpus", type=int, default=8)

    receipt = commands.add_parser("write-receipt")
    receipt.add_argument("--campaign", type=Path, required=True)
    receipt.add_argument("--kind", choices=("prep", "candidate", "refinement"), required=True)
    receipt.add_argument("--target", type=Path, required=True)
    receipt.add_argument("--candidate", choices=("balanced", "protocol-heavy", "recovery-heavy"))

    refine = commands.add_parser("prepare-refinement")
    refine.add_argument("--campaign", type=Path, required=True)
    refine.add_argument("--campaign-root", type=Path, required=True)
    refine.add_argument("--smoke-model-config", type=Path, required=True)
    refine.add_argument("--prime-root", type=Path, required=True)
    refine.add_argument("--selection-concurrency", type=int, default=32)
    refine.add_argument("--num-gpus", type=int, choices=(4, 8), default=8)

    finalize = commands.add_parser("finalize-refinement")
    finalize.add_argument("--campaign", type=Path, required=True)
    finalize.add_argument("--campaign-root", type=Path, required=True)
    finalize.add_argument("--device", default="cuda:0")

    fixed_v7 = commands.add_parser("prepare-browser-action-fixed-v7")
    fixed_v7.add_argument("--campaign", type=Path, required=True)
    fixed_v7.add_argument("--campaign-root", type=Path, required=True)
    fixed_v7.add_argument("--output", type=Path, required=True)
    fixed_v7.add_argument("--num-gpus", type=int, choices=(4,), default=4)

    fixed_v7_receipt = commands.add_parser("write-browser-action-fixed-v7-receipt")
    fixed_v7_receipt.add_argument("--campaign", type=Path, required=True)
    fixed_v7_receipt.add_argument("--campaign-root", type=Path, required=True)
    fixed_v7_receipt.add_argument("--stage", type=Path, required=True)
    fixed_v7_receipt.add_argument("--artifact-source-git-sha", required=True)
    fixed_v7_receipt.add_argument("--execution-source-git-sha", required=True)

    exact_lora = commands.add_parser("finalize-exact-lora")
    exact_lora.add_argument("--campaign", type=Path, required=True)
    exact_lora.add_argument("--campaign-root", type=Path, required=True)
    exact_lora.add_argument("--raw-base", type=Path, required=True)
    exact_lora.add_argument("--device", default="cuda:0")
    exact_lora.add_argument("--merge-failure-diagnostic", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    campaign = Campaign.load(args.campaign)

    if args.command == "validate-config":
        result = {"status": "ok", "campaign_digest": campaign.digest}
    elif args.command == "build-procedural-corpora":
        result = build_procedural_corpora(
            campaign,
            generator_root=args.generator_root,
            generator_wheel=args.generator_wheel,
            task_bank=args.task_bank,
            output_dir=args.output,
        )
    elif args.command == "materialize-prime":
        result = materialize_prime_dataset(
            campaign,
            source_jsonl=args.source_jsonl,
            source_manifest=args.source_manifest,
            smoke_report=args.smoke_report,
            stage=args.stage,
            candidate=args.candidate,
            output_dir=args.output,
            prime_root=args.prime_root,
        )
    elif args.command == "generate-sft-configs":
        result = generate_sft_configs(
            campaign,
            stage=args.stage,
            candidate=args.candidate,
            smoke_report=args.smoke_report,
            parent_model=args.parent_model,
            dataset_dir=args.dataset,
            output_dir=args.output,
            num_gpus=args.num_gpus,
        )
    elif args.command == "write-receipt":
        if args.kind == "prep":
            if args.candidate is not None:
                raise SystemExit("--candidate is invalid for a prep receipt")
            result = write_prep_receipt(campaign, args.target)
        elif args.kind == "candidate":
            if args.candidate is None:
                raise SystemExit("--candidate is required for a candidate receipt")
            result = write_candidate_receipt(campaign, args.target, args.candidate)
        else:
            if args.candidate is not None:
                raise SystemExit("--candidate is invalid for a refinement receipt")
            result = write_refinement_receipt(campaign, args.target)
    elif args.command == "prepare-refinement":
        result = prepare_refinement(
            campaign,
            campaign_root=args.campaign_root,
            smoke_model_config=args.smoke_model_config,
            prime_root=args.prime_root,
            selection_concurrency=args.selection_concurrency,
            num_gpus=args.num_gpus,
        )
    elif args.command == "finalize-refinement":
        result = finalize_refinement(campaign, campaign_root=args.campaign_root, device=args.device)
    elif args.command == "prepare-browser-action-fixed-v7":
        result = prepare_browser_action_fixed_v7(
            campaign,
            campaign_root=args.campaign_root,
            output_dir=args.output,
            num_gpus=args.num_gpus,
        )
    elif args.command == "write-browser-action-fixed-v7-receipt":
        result = write_browser_action_fixed_v7_receipt(
            campaign,
            campaign_root=args.campaign_root,
            stage_dir=args.stage,
            artifact_source_git_sha=args.artifact_source_git_sha,
            execution_source_git_sha=args.execution_source_git_sha,
        )
    elif args.command == "finalize-exact-lora":
        result = finalize_exact_lora(
            campaign,
            campaign_root=args.campaign_root,
            raw_base=args.raw_base,
            device=args.device,
            merge_failure_diagnostic=args.merge_failure_diagnostic,
        )
    else:  # pragma: no cover
        raise AssertionError(args.command)

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
