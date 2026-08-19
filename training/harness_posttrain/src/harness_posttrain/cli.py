"""Command-line interface for the isolated post-training package."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from .browser_action_continuation import prepare_browser_action_continuation
from .browser_action_continuation_receipt import (
    write_browser_action_continuation_receipt,
)
from .browser_action_curriculum import materialize_browser_action_curriculum
from .browser_action_finalization import finalize_browser_action_correction
from .browser_action_fixed_v5 import (
    prepare_browser_action_fixed_v5,
    write_browser_action_fixed_v5_receipt,
)
from .browser_action_fixed_v5_finalization import finalize_fixed_v5
from .browser_action_fixed_v5_gate import run_fixed_v5_gate
from .browser_action_fixed_v6 import (
    prepare_browser_action_fixed_v6,
    write_browser_action_fixed_v6_receipt,
)
from .browser_action_fixed_v7 import (
    prepare_browser_action_fixed_v7,
    write_browser_action_fixed_v7_receipt,
)
from .browser_action_selection import select_browser_action_checkpoint
from .browser_action_selection_v4 import select_browser_action_checkpoint_v4
from .cap64 import check_cap64_helper, default_destination, install_cap64_helper
from .config import Campaign
from .contract_refinement import collect_contract_rollouts, materialize_contract_refinement
from .exact_lora import finalize_exact_lora
from .finalize import finalize_refinement
from .gates import evaluate_selection_gate
from .opd import generate_optional_opd
from .post_sft import prepare_refinement
from .prime_data import materialize_prime_dataset
from .procedural_corpus import build_procedural_corpora
from .quick_data import build_quick_contract_data
from .receipts import write_candidate_receipt, write_prep_receipt, write_refinement_receipt
from .refinement import materialize_refinement
from .selection import select_sft_checkpoint
from .sft_data import materialize_targeted_sft
from .splits import freeze_splits
from .train_configs import generate_sft_configs
from .validate import validate_artifacts


def _campaign(args: argparse.Namespace) -> Campaign:
    return Campaign.load(args.campaign)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="harness-posttrain")
    sub = parser.add_subparsers(dest="command", required=True)

    validate_config = sub.add_parser("validate-config")
    validate_config.add_argument("--campaign", type=Path, required=True)

    freeze = sub.add_parser("freeze-splits")
    freeze.add_argument("--campaign", type=Path, required=True)
    freeze.add_argument("--inventory", type=Path, required=True)
    freeze.add_argument("--output", type=Path, required=True)

    quick = sub.add_parser("build-quick-contracts")
    quick.add_argument("--campaign", type=Path, required=True)
    quick.add_argument("--split-manifest", type=Path, required=True)
    quick.add_argument("--specs", type=Path, required=True)
    quick.add_argument("--output", type=Path, required=True)

    procedural = sub.add_parser("build-procedural-corpora")
    procedural.add_argument("--campaign", type=Path, required=True)
    generator = procedural.add_mutually_exclusive_group(required=True)
    generator.add_argument("--generator-root", type=Path)
    generator.add_argument("--generator-wheel", type=Path)
    procedural.add_argument("--task-bank", type=Path)
    procedural.add_argument("--output", type=Path, required=True)

    sft = sub.add_parser("materialize-sft")
    sft.add_argument("--campaign", type=Path, required=True)
    sft.add_argument(
        "--candidate",
        choices=("balanced", "protocol-heavy", "recovery-heavy"),
        required=True,
    )
    sft.add_argument("--split-manifest", type=Path, required=True)
    sft.add_argument("--contract", type=Path, required=True)
    sft.add_argument("--interaction", type=Path, required=True)
    sft.add_argument("--rehearsal", type=Path, required=True)
    sft.add_argument("--output", type=Path, required=True)

    refine = sub.add_parser("materialize-refinement")
    refine.add_argument("--campaign", type=Path, required=True)
    refine.add_argument("--split-manifest", type=Path, required=True)
    refine.add_argument("--episodes", type=Path, required=True)
    refine.add_argument("--corrections", type=Path, required=True)
    refine.add_argument("--rehearsal", type=Path, required=True)
    refine.add_argument("--output", type=Path, required=True)

    collect_contract = sub.add_parser("collect-contract-rollouts")
    collect_contract.add_argument("--campaign", type=Path, required=True)
    collect_contract.add_argument("--split-manifest", type=Path, required=True)
    collect_contract.add_argument("--tasks", type=Path, required=True)
    collect_contract.add_argument("--selected-checkpoint-manifest", type=Path, required=True)
    collect_contract.add_argument("--base-url", required=True)
    collect_contract.add_argument("--model", required=True)
    collect_contract.add_argument("--output", type=Path, required=True)
    collect_contract.add_argument("--api-key-env", default="OPENAI_API_KEY")
    collect_contract.add_argument("--concurrency", type=int, default=32)
    collect_contract.add_argument("--timeout-seconds", type=float, default=600.0)

    contract_refine = sub.add_parser("materialize-contract-refinement")
    contract_refine.add_argument("--campaign", type=Path, required=True)
    contract_refine.add_argument("--split-manifest", type=Path, required=True)
    contract_refine.add_argument("--tasks", type=Path, required=True)
    contract_refine.add_argument("--rollout-manifest", type=Path, required=True)
    contract_refine.add_argument("--rehearsal", type=Path, required=True)
    contract_refine.add_argument("--output", type=Path, required=True)

    browser_actions = sub.add_parser("materialize-browser-action-curriculum")
    browser_actions.add_argument("--campaign", type=Path, required=True)
    browser_actions.add_argument("--split-manifest", type=Path, required=True)
    browser_actions.add_argument("--rehearsal", type=Path, required=True)
    browser_actions.add_argument("--contract-replay", type=Path, required=True)
    browser_actions.add_argument("--output", type=Path, required=True)
    browser_actions.add_argument("--task-limit", type=int, default=256)
    browser_actions.add_argument("--contract-replay-limit", type=int, default=112)

    continuation = sub.add_parser("prepare-browser-action-continuation")
    continuation.add_argument("--campaign", type=Path, required=True)
    continuation.add_argument("--campaign-root", type=Path, required=True)
    continuation.add_argument("--curriculum-manifest", type=Path, required=True)
    continuation.add_argument("--dataset", type=Path, required=True)
    continuation.add_argument("--smoke-report", type=Path, required=True)
    continuation.add_argument("--output", type=Path, required=True)
    continuation.add_argument("--num-gpus", type=int, choices=(4, 8), default=4)

    continuation_receipt = sub.add_parser("write-browser-action-continuation-receipt")
    continuation_receipt.add_argument("--campaign", type=Path, required=True)
    continuation_receipt.add_argument("--campaign-root", type=Path, required=True)
    continuation_receipt.add_argument("--continuation-dir", type=Path, required=True)
    continuation_receipt.add_argument("--artifact-source-git-sha")
    continuation_receipt.add_argument("--execution-source-git-sha")

    fixed_v5 = sub.add_parser("prepare-browser-action-fixed-v5")
    fixed_v5.add_argument("--campaign", type=Path, required=True)
    fixed_v5.add_argument("--campaign-root", type=Path, required=True)
    fixed_v5.add_argument("--output", type=Path, required=True)
    fixed_v5.add_argument("--num-gpus", type=int, choices=(4,), default=4)

    fixed_v5_receipt = sub.add_parser("write-browser-action-fixed-v5-receipt")
    fixed_v5_receipt.add_argument("--campaign", type=Path, required=True)
    fixed_v5_receipt.add_argument("--campaign-root", type=Path, required=True)
    fixed_v5_receipt.add_argument("--stage", type=Path, required=True)
    fixed_v5_receipt.add_argument("--artifact-source-git-sha", required=True)
    fixed_v5_receipt.add_argument("--execution-source-git-sha", required=True)

    fixed_v6 = sub.add_parser("prepare-browser-action-fixed-v6")
    fixed_v6.add_argument("--campaign", type=Path, required=True)
    fixed_v6.add_argument("--campaign-root", type=Path, required=True)
    fixed_v6.add_argument("--output", type=Path, required=True)
    fixed_v6.add_argument("--num-gpus", type=int, choices=(4,), default=4)

    fixed_v6_receipt = sub.add_parser("write-browser-action-fixed-v6-receipt")
    fixed_v6_receipt.add_argument("--campaign", type=Path, required=True)
    fixed_v6_receipt.add_argument("--campaign-root", type=Path, required=True)
    fixed_v6_receipt.add_argument("--stage", type=Path, required=True)
    fixed_v6_receipt.add_argument("--artifact-source-git-sha", required=True)
    fixed_v6_receipt.add_argument("--execution-source-git-sha", required=True)

    fixed_v7 = sub.add_parser("prepare-browser-action-fixed-v7")
    fixed_v7.add_argument("--campaign", type=Path, required=True)
    fixed_v7.add_argument("--campaign-root", type=Path, required=True)
    fixed_v7.add_argument("--output", type=Path, required=True)
    fixed_v7.add_argument("--num-gpus", type=int, choices=(4,), default=4)

    fixed_v7_receipt = sub.add_parser("write-browser-action-fixed-v7-receipt")
    fixed_v7_receipt.add_argument("--campaign", type=Path, required=True)
    fixed_v7_receipt.add_argument("--campaign-root", type=Path, required=True)
    fixed_v7_receipt.add_argument("--stage", type=Path, required=True)
    fixed_v7_receipt.add_argument("--artifact-source-git-sha", required=True)
    fixed_v7_receipt.add_argument("--execution-source-git-sha", required=True)

    fixed_v5_gate = sub.add_parser("gate-browser-action-fixed-v5")
    fixed_v5_gate.add_argument("--campaign", type=Path, required=True)
    fixed_v5_gate.add_argument("--campaign-root", type=Path, required=True)
    fixed_v5_gate.add_argument("--training-receipt", type=Path, required=True)
    fixed_v5_gate.add_argument("--baseline-selection-dir", type=Path, required=True)
    fixed_v5_gate.add_argument("--output", type=Path, required=True)
    fixed_v5_gate.add_argument("--concurrency", type=int, default=32)
    fixed_v5_gate.add_argument("--num-gpus", type=int, choices=(4, 8), default=4)
    fixed_v5_gate.add_argument("--port", type=int, default=8000)

    fixed_v5_finalize = sub.add_parser("finalize-browser-action-fixed-v5")
    fixed_v5_finalize.add_argument("--campaign", type=Path, required=True)
    fixed_v5_finalize.add_argument("--campaign-root", type=Path, required=True)
    fixed_v5_finalize.add_argument("--training-receipt", type=Path, required=True)
    fixed_v5_finalize.add_argument("--gate-manifest", type=Path, required=True)
    fixed_v5_finalize.add_argument("--baseline-selection-dir", type=Path, required=True)
    fixed_v5_finalize.add_argument("--raw-base", type=Path, required=True)

    action_selection = sub.add_parser("select-browser-action-checkpoint")
    action_selection.add_argument("--campaign", type=Path, required=True)
    action_selection.add_argument("--campaign-root", type=Path, required=True)
    action_selection.add_argument("--continuation-dir", type=Path, required=True)
    action_selection.add_argument("--output", type=Path, required=True)
    action_selection.add_argument("--concurrency", type=int, default=64)
    action_selection.add_argument("--num-gpus", type=int, choices=(4, 8), default=4)
    action_selection.add_argument("--port", type=int, default=8000)

    adaptive_action_selection = sub.add_parser("select-browser-action-checkpoint-v4")
    adaptive_action_selection.add_argument("--campaign", type=Path, required=True)
    adaptive_action_selection.add_argument("--campaign-root", type=Path, required=True)
    adaptive_action_selection.add_argument("--continuation-dir", type=Path, required=True)
    adaptive_action_selection.add_argument("--v3-selection-dir", type=Path, required=True)
    adaptive_action_selection.add_argument("--output", type=Path, required=True)
    adaptive_action_selection.add_argument("--concurrency", type=int, default=10)
    adaptive_action_selection.add_argument("--num-gpus", type=int, choices=(4, 8), default=4)
    adaptive_action_selection.add_argument("--port", type=int, default=8000)

    action_finalization = sub.add_parser("finalize-browser-action-correction")
    action_finalization.add_argument("--campaign", type=Path, required=True)
    action_finalization.add_argument("--campaign-root", type=Path, required=True)
    action_finalization.add_argument("--continuation-receipt", type=Path, required=True)
    action_finalization.add_argument("--selection-manifest", type=Path, required=True)
    action_finalization.add_argument("--raw-base", type=Path, required=True)

    parquet = sub.add_parser("materialize-prime")
    parquet.add_argument("--campaign", type=Path, required=True)
    parquet.add_argument("--source-jsonl", type=Path, required=True)
    parquet.add_argument("--source-manifest", type=Path, required=True)
    parquet.add_argument("--smoke-report", type=Path, required=True)
    parquet.add_argument("--stage", choices=("targeted_sft", "refinement"), required=True)
    parquet.add_argument("--candidate", choices=("balanced", "protocol-heavy", "recovery-heavy"))
    parquet.add_argument("--output", type=Path, required=True)
    parquet.add_argument("--prime-root", type=Path)

    configs = sub.add_parser("generate-sft-configs")
    configs.add_argument("--campaign", type=Path, required=True)
    configs.add_argument("--stage", choices=("targeted_sft", "refinement"), required=True)
    configs.add_argument("--candidate", choices=("balanced", "protocol-heavy", "recovery-heavy"))
    configs.add_argument("--smoke-report", type=Path, required=True)
    configs.add_argument("--parent-model", type=Path, required=True)
    configs.add_argument("--dataset", type=Path, required=True)
    configs.add_argument("--output", type=Path, required=True)
    configs.add_argument("--num-gpus", type=int, default=8)

    opd = sub.add_parser("generate-opd-smoke")
    opd.add_argument("--campaign", type=Path, required=True)
    opd.add_argument("--split-manifest", type=Path, required=True)
    opd.add_argument("--smoke-report", type=Path, required=True)
    opd.add_argument("--student", type=Path, required=True)
    opd.add_argument("--teacher", type=Path, required=True)
    opd.add_argument("--replay", type=Path, required=True)
    opd.add_argument("--output", type=Path, required=True)
    opd.add_argument("--enable-optional-opd", action="store_true")

    artifacts = sub.add_parser("validate-artifacts")
    artifacts.add_argument("--campaign", type=Path, required=True)
    artifacts.add_argument("--split-manifest", type=Path)
    artifacts.add_argument("--dataset", type=Path)
    artifacts.add_argument("--train-plan", type=Path)

    cap64 = sub.add_parser("cap64-helper")
    cap64.add_argument("--campaign", type=Path, required=True)
    cap64.add_argument("--source", type=Path, required=True)
    cap64.add_argument("--destination", type=Path, default=default_destination())
    cap64.add_argument("--check", action="store_true")

    gate = sub.add_parser("evaluate-gate")
    gate.add_argument("--campaign", type=Path, required=True)
    gate.add_argument("--parent", type=Path, required=True)
    gate.add_argument("--candidate", type=Path, required=True)

    select = sub.add_parser("select-sft-checkpoint")
    select.add_argument("--campaign", type=Path, required=True)
    select.add_argument("--campaign-root", type=Path, required=True)
    select.add_argument("--output", type=Path, required=True)
    select.add_argument("--concurrency", type=int, default=32)
    select.add_argument("--num-gpus", type=int, choices=(4, 8), default=8)

    receipt = sub.add_parser("write-receipt")
    receipt.add_argument("--campaign", type=Path, required=True)
    receipt.add_argument("--kind", choices=("prep", "candidate", "refinement"), required=True)
    receipt.add_argument("--target", type=Path, required=True)
    receipt.add_argument("--candidate", choices=("balanced", "protocol-heavy", "recovery-heavy"))

    post_sft = sub.add_parser("prepare-refinement")
    post_sft.add_argument("--campaign", type=Path, required=True)
    post_sft.add_argument("--campaign-root", type=Path, required=True)
    post_sft.add_argument("--smoke-model-config", type=Path, required=True)
    post_sft.add_argument("--prime-root", type=Path, required=True)
    post_sft.add_argument("--selection-concurrency", type=int, default=32)
    post_sft.add_argument("--num-gpus", type=int, choices=(4, 8), default=8)

    finalize = sub.add_parser("finalize-refinement")
    finalize.add_argument("--campaign", type=Path, required=True)
    finalize.add_argument("--campaign-root", type=Path, required=True)
    finalize.add_argument("--device", default="cuda:0")

    exact_lora = sub.add_parser("finalize-exact-lora")
    exact_lora.add_argument("--campaign", type=Path, required=True)
    exact_lora.add_argument("--campaign-root", type=Path, required=True)
    exact_lora.add_argument("--raw-base", type=Path, required=True)
    exact_lora.add_argument("--device", default="cuda:0")
    exact_lora.add_argument("--merge-failure-diagnostic", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    campaign = _campaign(args)
    if args.command == "validate-config":
        result = {"status": "ok", "campaign_digest": campaign.digest}
    elif args.command == "freeze-splits":
        result = freeze_splits(campaign, inventory_path=args.inventory, output_dir=args.output)
    elif args.command == "build-quick-contracts":
        result = build_quick_contract_data(
            campaign,
            split_manifest_path=args.split_manifest,
            specs_path=args.specs,
            output_dir=args.output,
        )
    elif args.command == "build-procedural-corpora":
        result = build_procedural_corpora(
            campaign,
            generator_root=args.generator_root,
            generator_wheel=args.generator_wheel,
            task_bank=args.task_bank,
            output_dir=args.output,
        )
    elif args.command == "materialize-sft":
        result = materialize_targeted_sft(
            campaign,
            candidate=args.candidate,
            split_manifest_path=args.split_manifest,
            contract_path=args.contract,
            interaction_path=args.interaction,
            rehearsal_path=args.rehearsal,
            output_dir=args.output,
        )
    elif args.command == "materialize-refinement":
        result = materialize_refinement(
            campaign,
            split_manifest_path=args.split_manifest,
            episodes_path=args.episodes,
            corrections_path=args.corrections,
            rehearsal_path=args.rehearsal,
            output_dir=args.output,
        )
    elif args.command == "collect-contract-rollouts":
        result = collect_contract_rollouts(
            campaign,
            split_manifest_path=args.split_manifest,
            tasks_path=args.tasks,
            selected_checkpoint_manifest=args.selected_checkpoint_manifest,
            base_url=args.base_url,
            model=args.model,
            output_dir=args.output,
            api_key_env=args.api_key_env,
            concurrency=args.concurrency,
            timeout_seconds=args.timeout_seconds,
        )
    elif args.command == "materialize-contract-refinement":
        result = materialize_contract_refinement(
            campaign,
            split_manifest_path=args.split_manifest,
            tasks_path=args.tasks,
            rollout_manifest_path=args.rollout_manifest,
            rehearsal_path=args.rehearsal,
            output_dir=args.output,
        )
    elif args.command == "materialize-browser-action-curriculum":
        result = materialize_browser_action_curriculum(
            campaign,
            split_manifest_path=args.split_manifest,
            rehearsal_path=args.rehearsal,
            contract_replay_path=args.contract_replay,
            output_dir=args.output,
            task_limit=args.task_limit,
            contract_replay_limit=args.contract_replay_limit,
        )
    elif args.command == "prepare-browser-action-continuation":
        result = prepare_browser_action_continuation(
            campaign,
            campaign_root=args.campaign_root,
            curriculum_manifest=args.curriculum_manifest,
            dataset_dir=args.dataset,
            smoke_report=args.smoke_report,
            output_dir=args.output,
            num_gpus=args.num_gpus,
        )
    elif args.command == "write-browser-action-continuation-receipt":
        result = write_browser_action_continuation_receipt(
            campaign,
            campaign_root=args.campaign_root,
            continuation_dir=args.continuation_dir,
            artifact_source_git_sha=args.artifact_source_git_sha,
            execution_source_git_sha=args.execution_source_git_sha,
        )
    elif args.command == "prepare-browser-action-fixed-v5":
        result = prepare_browser_action_fixed_v5(
            campaign,
            campaign_root=args.campaign_root,
            output_dir=args.output,
            num_gpus=args.num_gpus,
        )
    elif args.command == "write-browser-action-fixed-v5-receipt":
        result = write_browser_action_fixed_v5_receipt(
            campaign,
            campaign_root=args.campaign_root,
            stage_dir=args.stage,
            artifact_source_git_sha=args.artifact_source_git_sha,
            execution_source_git_sha=args.execution_source_git_sha,
        )
    elif args.command == "prepare-browser-action-fixed-v6":
        result = prepare_browser_action_fixed_v6(
            campaign,
            campaign_root=args.campaign_root,
            output_dir=args.output,
            num_gpus=args.num_gpus,
        )
    elif args.command == "write-browser-action-fixed-v6-receipt":
        result = write_browser_action_fixed_v6_receipt(
            campaign,
            campaign_root=args.campaign_root,
            stage_dir=args.stage,
            artifact_source_git_sha=args.artifact_source_git_sha,
            execution_source_git_sha=args.execution_source_git_sha,
        )
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
    elif args.command == "gate-browser-action-fixed-v5":
        result = run_fixed_v5_gate(
            campaign,
            campaign_root=args.campaign_root,
            training_receipt=args.training_receipt,
            baseline_selection_dir=args.baseline_selection_dir,
            output_dir=args.output,
            concurrency=args.concurrency,
            num_gpus=args.num_gpus,
            port=args.port,
        )
    elif args.command == "finalize-browser-action-fixed-v5":
        result = finalize_fixed_v5(
            campaign,
            campaign_root=args.campaign_root,
            training_receipt=args.training_receipt,
            gate_manifest=args.gate_manifest,
            baseline_selection_dir=args.baseline_selection_dir,
            raw_base=args.raw_base,
        )
    elif args.command == "select-browser-action-checkpoint":
        result = select_browser_action_checkpoint(
            campaign,
            campaign_root=args.campaign_root,
            continuation_dir=args.continuation_dir,
            output_dir=args.output,
            concurrency=args.concurrency,
            num_gpus=args.num_gpus,
            port=args.port,
        )
    elif args.command == "select-browser-action-checkpoint-v4":
        result = select_browser_action_checkpoint_v4(
            campaign,
            campaign_root=args.campaign_root,
            continuation_dir=args.continuation_dir,
            v3_selection_dir=args.v3_selection_dir,
            output_dir=args.output,
            concurrency=args.concurrency,
            num_gpus=args.num_gpus,
            port=args.port,
        )
    elif args.command == "finalize-browser-action-correction":
        result = finalize_browser_action_correction(
            campaign,
            campaign_root=args.campaign_root,
            continuation_receipt=args.continuation_receipt,
            selection_manifest=args.selection_manifest,
            raw_base=args.raw_base,
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
    elif args.command == "generate-opd-smoke":
        result = generate_optional_opd(
            campaign,
            enabled=args.enable_optional_opd,
            split_manifest_path=args.split_manifest,
            smoke_report=args.smoke_report,
            student_model=args.student,
            teacher_model=args.teacher,
            replay_path=args.replay,
            output_dir=args.output,
        )
    elif args.command == "validate-artifacts":
        result = validate_artifacts(
            campaign,
            split_manifest=args.split_manifest,
            dataset=args.dataset,
            train_plan=args.train_plan,
        )
    elif args.command == "cap64-helper":
        if args.check:
            result = check_cap64_helper(source=args.source, destination=args.destination)
        else:
            result = install_cap64_helper(source=args.source, destination=args.destination)
    elif args.command == "evaluate-gate":
        result = evaluate_selection_gate(
            campaign, parent_path=args.parent, candidate_path=args.candidate
        )
    elif args.command == "select-sft-checkpoint":
        result = select_sft_checkpoint(
            campaign,
            campaign_root=args.campaign_root,
            output_dir=args.output,
            concurrency=args.concurrency,
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
        result = finalize_refinement(
            campaign,
            campaign_root=args.campaign_root,
            device=args.device,
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


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
