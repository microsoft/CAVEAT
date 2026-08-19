"""Finalize the reward-filtered refinement into an evaluation-ready model."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .artifacts import (
    ArtifactError,
    canonical_json,
    publish_json,
    read_json,
    sha256_bytes,
    sha256_file,
    tree_digest,
)
from .config import Campaign
from .parent_merge import PARENT_MERGE_SCHEMA, checkpoint_manifest, verify_parent_aware_merge

FINAL_MODEL_MANIFEST_SCHEMA = "caveat-27b.final-inference-model.v1"


def _checkpoint(receipt: dict[str, Any], update: int) -> dict[str, Any]:
    values = receipt.get("checkpoints")
    if not isinstance(values, list):
        raise ArtifactError("refinement receipt has no checkpoint inventory")
    matches = [row for row in values if isinstance(row, dict) and row.get("update") == update]
    if len(matches) != 1:
        raise ArtifactError(f"refinement receipt does not attest exactly one step-{update} adapter")
    return matches[0]


def finalize_refinement(
    campaign: Campaign,
    *,
    campaign_root: str | Path,
    device: str = "cuda:0",
) -> dict[str, Any]:
    """Require the training receipt, merge step 20 into its parent, and freeze inference."""

    root = Path(campaign_root).resolve()
    receipt_path = root / "refinement/training_receipt.json"
    receipt = read_json(receipt_path)
    if (
        not isinstance(receipt, dict)
        or receipt.get("schema") != "caveat-27b.refinement-training-receipt.v1"
        or receipt.get("status") != "ok"
        or receipt.get("campaign_digest") != campaign.digest
        or receipt.get("optimizer_updates") != 20
    ):
        raise ArtifactError("finalization requires the exact successful refinement receipt")

    post_path = root / "post_sft_receipt.json"
    if receipt.get("post_sft_receipt_sha256") != sha256_file(post_path):
        raise ArtifactError("post-SFT receipt changed after refinement training")
    parent = (root / "selected/merged").resolve()
    if Path(str(receipt.get("parent_model", ""))).resolve() != parent or receipt.get(
        "parent_merge_provenance_sha256"
    ) != sha256_file(parent / "merge_provenance.json"):
        raise ArtifactError("selected SFT parent changed after refinement training")

    final_checkpoint = _checkpoint(receipt, 20)
    adapter = Path(str(final_checkpoint.get("path", ""))).resolve()
    if not adapter.is_dir():
        raise ArtifactError("attested final refinement adapter is absent")
    if final_checkpoint.get("stable_marker_sha256") != sha256_file(adapter.parent / "STABLE"):
        raise ArtifactError("final refinement completion marker changed after its receipt")
    adapter_manifest = checkpoint_manifest(adapter)
    adapter_tree_sha256 = sha256_bytes(canonical_json(adapter_manifest).encode())
    adapter_files = [item for item in adapter.rglob("*") if item.is_file()]
    if final_checkpoint.get("files") != len(adapter_files) or final_checkpoint.get(
        "sha256"
    ) != tree_digest(adapter_files, adapter):
        raise ArtifactError("final refinement adapter bytes changed after its receipt")

    output = root / "final/model"
    merge = verify_parent_aware_merge(parent, adapter, output, device=device)
    if (
        merge.get("status") != "ok"
        or merge.get("schema") != PARENT_MERGE_SCHEMA
        or merge.get("parent_model") != str(parent)
        or merge.get("adapter_path") != str(adapter)
    ):
        raise ArtifactError("parent-aware refinement merge did not pass verification")
    model_manifest = merge.get("merged_manifest")
    if not isinstance(model_manifest, dict) or model_manifest != checkpoint_manifest(output):
        raise ArtifactError("final model bytes differ from merge provenance")

    result = {
        "schema": FINAL_MODEL_MANIFEST_SCHEMA,
        "status": "ok",
        "campaign_digest": campaign.digest,
        "objective_scaffold": campaign.campaign["objective"]["scaffold"],
        "model_id": campaign.model["model_id"],
        "base_revision": campaign.model["revision"],
        "model_path": str(output),
        "model_tree_sha256": sha256_bytes(canonical_json(model_manifest).encode()),
        "model_files": len(model_manifest),
        "merge_provenance_sha256": sha256_file(output / "merge_provenance.json"),
        "refinement_receipt_sha256": sha256_file(receipt_path),
        "parent_model": str(parent),
        "parent_merge_provenance_sha256": sha256_file(parent / "merge_provenance.json"),
        "refinement_adapter": str(adapter),
        "refinement_adapter_tree_sha256": adapter_tree_sha256,
        "refinement_update": 20,
        "inference": {
            "api": "openai_chat_completions",
            "served_model_name": "caveat-27b",
            "dtype": campaign.model["dtype"],
            "native_context_tokens": campaign.model["native_context_tokens"],
            "language_model_only": campaign.model["language_model_only"],
            "reasoning_parser": campaign.model["reasoning_parser"],
            "tool_call_parser": campaign.model["tool_call_parser"],
            "enable_auto_tool_choice": True,
            "enable_prefix_caching": campaign.model["enable_prefix_caching"],
            "preserve_thinking": campaign.model["preserve_thinking"],
        },
    }
    publish_json(root / "final/inference_manifest.json", result)
    return result
