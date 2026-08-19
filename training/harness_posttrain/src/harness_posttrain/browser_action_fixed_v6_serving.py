"""Validate and describe the fixed-v6 paired exact-LoRA endpoint.

This is a pre-gate structural stage.  It may start inference for the frozen
compact behavioral gate, but it does not authorize marketplace evaluation.
Only the host-side gate watcher can promote these exact bytes after the frozen
behavioral gate passes.
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .artifacts import (
    ArtifactError,
    canonical_json,
    publish_json,
    read_json,
    sha256_bytes,
    sha256_file,
)
from .browser_action_continuation import _tree_identity
from .browser_action_finalization import _tokenizer_attestation
from .browser_action_fixed_v6 import (
    FIXED_V6_RECEIPT_SCHEMA,
    write_browser_action_fixed_v6_receipt,
)
from .config import Campaign
from .exact_lora import (
    _verified_raw_base,
    component_identity,
    exact_lora_composite_sha256,
)

FIXED_V6_PRE_GATE_MANIFEST_SCHEMA = (
    "harness-posttrain.browser-action-fixed-v6-pre-gate-exact-lora.v1"
)
FIXED_V6_PRE_GATE_RECEIPT_SCHEMA = (
    "harness-posttrain.browser-action-fixed-v6-pre-gate-manifest-receipt.v1"
)

_GIT_SHA = re.compile(r"[0-9a-f]{40}")


def _same_identity(actual: Mapping[str, Any], expected: Mapping[str, Any], label: str) -> None:
    for field in ("path", "files", "bytes", "tree_sha256"):
        if actual.get(field) != expected.get(field):
            raise ArtifactError(f"fixed-v6 {label} identity changed: {field}")


def _arm(
    *,
    name: str,
    update: int,
    parent: Path,
    adapter: Path,
    tokenizer: Mapping[str, Any],
    served_model_name: str,
) -> dict[str, Any]:
    parent_identity = component_identity(parent)
    adapter_identity = component_identity(adapter)
    config_sha = sha256_file(adapter / "adapter_config.json")
    composite = exact_lora_composite_sha256(
        parent_tree_sha256=parent_identity["tree_sha256"],
        adapter_tree_sha256=adapter_identity["tree_sha256"],
        adapter_config_sha256=config_sha,
        tokenizer_json_sha256=str(tokenizer["shared_tokenizer_json_sha256"]),
        chat_template_sha256=str(tokenizer["chat_template_sha256"]),
        dtype="bfloat16",
    )
    return {
        "name": name,
        "update": update,
        "parent": parent_identity,
        "adapter": adapter_identity,
        "adapter_config_sha256": config_sha,
        "tokenizer_json_sha256": tokenizer["shared_tokenizer_json_sha256"],
        "chat_template_sha256": tokenizer["chat_template_sha256"],
        "dtype": "bfloat16",
        "composite_sha256": composite,
        "served_model_name": served_model_name,
    }


def prepare_fixed_v6_pair_manifest(
    campaign: Campaign,
    *,
    campaign_root: str | Path,
    training_receipt: str | Path,
    raw_base: str | Path,
    artifact_source_git_sha: str,
    execution_source_git_sha: str,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Revalidate training and publish the immutable pre-gate pair."""

    if _GIT_SHA.fullmatch(artifact_source_git_sha) is None:
        raise ArtifactError("fixed-v6 artifact source SHA is invalid")
    if _GIT_SHA.fullmatch(execution_source_git_sha) is None:
        raise ArtifactError("fixed-v6 execution source SHA is invalid")
    root = Path(campaign_root).resolve()
    stage = (root / "browser_action_fixed_v6" / artifact_source_git_sha).resolve()
    receipt_path = Path(training_receipt).resolve()
    if receipt_path != stage / "training/training_receipt.json":
        raise ArtifactError("fixed-v6 receipt is outside the source-SHA stage")
    before = read_json(receipt_path)
    training_execution_sha = before.get("execution_source_git_sha")
    if (
        before.get("artifact_source_git_sha") != artifact_source_git_sha
        or training_execution_sha != artifact_source_git_sha
    ):
        raise ArtifactError("fixed-v6 training receipt source provenance changed")
    validated = write_browser_action_fixed_v6_receipt(
        campaign,
        campaign_root=root,
        stage_dir=stage,
        artifact_source_git_sha=artifact_source_git_sha,
        # Revalidation must reproduce the immutable training receipt with the
        # source that executed training.  The newer serving source is bound
        # separately in the pre-gate manifest and must never rewrite history.
        execution_source_git_sha=str(training_execution_sha),
    )
    if before != validated or read_json(receipt_path) != validated:
        raise ArtifactError("fixed-v6 receipt changed during serving validation")
    candidate_receipt = validated.get("candidate")
    source_receipt = validated.get("source_step20_adapter")
    if (
        validated.get("schema") != FIXED_V6_RECEIPT_SCHEMA
        or validated.get("status") != "ok"
        or validated.get("selection_performed") is not False
        or validated.get("amazon_outcomes_consulted") is not False
        or validated.get("optimizer_updates") != 24
        or validated.get("new_optimizer_updates") != 4
        or validated.get("checkpoint_updates") != [24]
        or not isinstance(candidate_receipt, Mapping)
        or not isinstance(source_receipt, Mapping)
        or candidate_receipt.get("name") != "step24"
        or candidate_receipt.get("update") != 24
    ):
        raise ArtifactError("fixed-v6 training receipt is incompatible with paired serving")

    parent = Path(str(validated.get("parent_model", ""))).resolve()
    source_adapter = Path(str(source_receipt.get("path", ""))).resolve()
    candidate_adapter = Path(str(candidate_receipt.get("path", ""))).resolve()
    # The training receipt is deliberately created with the byte-complete,
    # symlink-rejecting continuation tree identity.  ``component_identity`` is
    # the logical exact-LoRA identity and intentionally has no aggregate
    # ``bytes`` field, so it cannot validate the receipt schema itself.
    _same_identity(_tree_identity(source_adapter), source_receipt, "step20 adapter")
    _same_identity(_tree_identity(candidate_adapter), candidate_receipt, "step24 adapter")
    if sha256_file(candidate_adapter / "adapter_config.json") != candidate_receipt.get(
        "adapter_config_sha256"
    ):
        raise ArtifactError("fixed-v6 candidate adapter configuration changed")
    raw = Path(raw_base).resolve()
    _verified_raw_base(campaign, root, raw)
    tokenizer = _tokenizer_attestation(raw, parent)["semantic"]
    step20 = _arm(
        name="step20",
        update=20,
        parent=parent,
        adapter=source_adapter,
        tokenizer=tokenizer,
        served_model_name="qwen35-browser-action-step20-exact-lora",
    )
    fixed_v6 = _arm(
        name="fixed_v6",
        update=24,
        parent=parent,
        adapter=candidate_adapter,
        tokenizer=tokenizer,
        served_model_name=(
            f"qwen35-browser-action-fixed-v6-{artifact_source_git_sha[:12]}-exact-lora"
        ),
    )
    if step20["composite_sha256"] == fixed_v6["composite_sha256"]:
        raise ArtifactError("fixed-v6 candidate aliases step20")

    output = Path(output_dir).resolve()
    if output != stage / "pre_gate":
        raise ArtifactError("fixed-v6 pre-gate output path is not canonical")
    manifest_path = output / "exact_lora_manifest.json"
    receipt_output = output / "exact_lora_manifest_receipt.json"
    manifest = {
        "schema": FIXED_V6_PRE_GATE_MANIFEST_SCHEMA,
        "status": "pre_gate_only",
        "stage": "browser_action_fixed_v6",
        "campaign_digest": campaign.digest,
        "artifact_source_git_sha": artifact_source_git_sha,
        "execution_source_git_sha": execution_source_git_sha,
        "training_receipt": {
            "path": str(receipt_path),
            "sha256": sha256_file(receipt_path),
            "receipt_body_sha256": validated["receipt_body_sha256"],
        },
        "arms": {"step20": step20, "fixed_v6": fixed_v6},
        "inference": {
            "api": "openai_chat_completions",
            "dtype": "bfloat16",
            "max_model_len": 32768,
            "reasoning_parser": "qwen3",
            "tool_call_parser": "qwen3_coder",
            "enable_auto_tool_choice": True,
            "enable_prefix_caching": False,
            "data_parallel_size": 4,
            "api_server_count": 4,
            "max_loras": 2,
            "max_cpu_loras": 2,
            "max_lora_rank": 64,
        },
        "science_policy": {
            "outcome_blind": True,
            "selection_performed": False,
            "amazon_outcomes_consulted": False,
            "behavioral_gate_pass_required_before_marketplace_evaluation": True,
            "not_a_final_publication": True,
        },
    }
    publish_json(manifest_path, manifest)
    receipt_body = {
        "schema": FIXED_V6_PRE_GATE_RECEIPT_SCHEMA,
        "status": "ok",
        "manifest_path": str(manifest_path),
        "manifest_sha256": sha256_file(manifest_path),
        "training_receipt_sha256": sha256_file(receipt_path),
        "step20_composite_sha256": step20["composite_sha256"],
        "fixed_v6_composite_sha256": fixed_v6["composite_sha256"],
        "behavioral_gate_passed": False,
        "marketplace_evaluation_authorized": False,
    }
    manifest_receipt = {
        **receipt_body,
        "receipt_body_sha256": sha256_bytes(canonical_json(receipt_body).encode()),
    }
    publish_json(receipt_output, manifest_receipt)
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--training-receipt", type=Path, required=True)
    parser.add_argument("--raw-base", type=Path, required=True)
    parser.add_argument("--artifact-source-git-sha", required=True)
    parser.add_argument("--execution-source-git-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> None:
    arguments = _parser().parse_args()
    campaign = Campaign.load(arguments.campaign)
    manifest = prepare_fixed_v6_pair_manifest(
        campaign,
        campaign_root=arguments.campaign_root,
        training_receipt=arguments.training_receipt,
        raw_base=arguments.raw_base,
        artifact_source_git_sha=arguments.artifact_source_git_sha,
        execution_source_git_sha=arguments.execution_source_git_sha,
        output_dir=arguments.output,
    )
    step20, fixed_v6 = manifest["arms"]["step20"], manifest["arms"]["fixed_v6"]
    print(step20["parent"]["path"])
    print(step20["adapter"]["path"])
    print(step20["served_model_name"])
    print(fixed_v6["adapter"]["path"])
    print(fixed_v6["served_model_name"])


if __name__ == "__main__":
    main()
