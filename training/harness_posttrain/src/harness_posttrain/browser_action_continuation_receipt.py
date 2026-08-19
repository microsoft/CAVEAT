"""Fail-closed receipt for the completed browser-action continuation.

The preparation plan is only a promise about a future run.  This module turns
that promise into a content-addressed receipt after PRIME has produced every
predeclared adapter.  It also rechecks the original step-20 adapter and DCP so
the continuation cannot silently rewrite the model it claims to resume from.
"""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .artifacts import ArtifactError, publish_json, read_json, sha256_file
from .browser_action_continuation import (
    CONTINUATION_PLAN_SCHEMA,
    _legacy_tree_digest,
    _tree_identity,
    _verified_dataset,
    _verified_source,
)
from .config import Campaign

CONTINUATION_RECEIPT_SCHEMA = (
    "harness-posttrain.browser-action-continuation-training-receipt.v1"
)

_SOURCE_STEP = 20
_CHECKPOINT_STEPS = [22, 24, 26, 28]
_FINAL_STEP = 28
_LEARNING_RATE = 2.0e-6
_GIT_SHA = re.compile(r"[0-9a-f]{40}")


def _git_sha(value: str | None, *, label: str) -> str:
    if value is None or _GIT_SHA.fullmatch(value) is None:
        raise ArtifactError(f"{label} must be a 40-character lowercase Git SHA")
    return value


def _same_tree(
    actual: Mapping[str, Any], expected: Mapping[str, Any], *, label: str
) -> None:
    for key in ("files", "bytes", "tree_sha256"):
        if actual.get(key) != expected.get(key):
            raise ArtifactError(f"{label} content identity changed")


def _parse_config(path: Path) -> dict[str, Any]:
    try:
        value = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ArtifactError("browser-action continuation configuration is invalid") from exc
    if not isinstance(value, dict):  # pragma: no cover - tomllib returns a dict
        raise ArtifactError("browser-action continuation configuration is invalid")
    return value


def _verify_source_identities(
    *,
    root: Path,
    continuation: Path,
    campaign_digest: str,
    artifact_source: str,
    execution_source: str,
) -> dict[str, str]:
    campaign_source = _git_sha(root.name, label="campaign artifact source")
    if continuation.name != "training" or continuation.parent.parent.name != (
        "browser_action_correction"
    ):
        raise ArtifactError("continuation directory is outside its reviewed stage layout")
    path_artifact_source = _git_sha(
        continuation.parent.name, label="continuation artifact source"
    )
    if artifact_source != path_artifact_source:
        raise ArtifactError("artifact source SHA differs from the continuation stage path")

    prep = read_json(root / "prep_receipt.json")
    post = read_json(root / "post_sft_receipt.json")
    refinement = read_json(root / "refinement/training_receipt.json")
    if (
        not isinstance(prep, dict)
        or prep.get("status") != "ok"
        or prep.get("campaign_digest") != campaign_digest
        or prep.get("source_git_sha") != campaign_source
        or not isinstance(post, dict)
        or post.get("campaign_digest") != campaign_digest
        or post.get("artifact_source_git_sha") != campaign_source
        or not isinstance(refinement, dict)
        or refinement.get("campaign_digest") != campaign_digest
    ):
        raise ArtifactError("original campaign artifact source identity is inconsistent")
    post_execution = _git_sha(
        post.get("execution_source_git_sha"), label="post-SFT execution source"
    )
    refinement_execution = _git_sha(
        refinement.get("execution_source_git_sha"),
        label="refinement execution source",
    )
    return {
        "campaign_artifact_source_git_sha": campaign_source,
        "post_sft_execution_source_git_sha": post_execution,
        "refinement_execution_source_git_sha": refinement_execution,
        "artifact_source_git_sha": artifact_source,
        "execution_source_git_sha": execution_source,
    }


def _verify_plan(
    campaign: Campaign,
    *,
    root: Path,
    continuation: Path,
    source: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[str]]:
    plan_path = continuation / "plan.json"
    config_path = continuation / "browser_action_continuation.toml"
    curriculum_manifest = continuation.parent / "curriculum/manifest.json"
    dataset = continuation.parent / "prime"
    smoke_report = root / "smoke/smoke_report.json"
    plan = read_json(plan_path)
    if not isinstance(plan, dict):
        raise ArtifactError("browser-action continuation plan is invalid")
    plan_training = plan.get("training")
    plan_source = plan.get("source")
    if not isinstance(plan_training, dict) or not isinstance(plan_source, dict):
        raise ArtifactError("browser-action continuation plan has invalid nested fields")
    expected_parent = (root / "selected/merged").resolve()
    expected_candidates = [
        {
            "update": step,
            "adapter": str(
                (continuation / f"prime_output/weights/step_{step}/lora_adapters").resolve()
            ),
        }
        for step in _CHECKPOINT_STEPS
    ]
    expected_training = {
        "num_gpus": plan_training.get("num_gpus"),
        "resume_step": _SOURCE_STEP,
        "new_optimizer_updates": _FINAL_STEP - _SOURCE_STEP,
        "max_steps": _FINAL_STEP,
        "learning_rate": _LEARNING_RATE,
        "checkpoint_updates": _CHECKPOINT_STEPS,
        "restore_model": True,
        "restore_progress": True,
        "restore_optimizer": False,
        "restore_scheduler": False,
        "restore_dataloader": False,
    }
    if (
        plan.get("schema") != CONTINUATION_PLAN_SCHEMA
        or plan.get("stage") != "browser_action_continuation"
        or plan.get("status") != "prepared"
        or plan.get("campaign_digest") != campaign.digest
        or plan.get("model_revision") != campaign.model["revision"]
        or Path(str(plan.get("parent_model", ""))).resolve() != expected_parent
        or Path(str(plan_source.get("campaign_root", ""))).resolve() != root
        or Path(str(plan.get("dataset", ""))).resolve() != dataset.resolve()
        or Path(str(plan.get("config", ""))).resolve() != config_path.resolve()
        or plan.get("config_sha256") != sha256_file(config_path)
        or plan.get("training") != expected_training
        or expected_training["num_gpus"] not in {4, 8}
        or plan.get("candidates") != expected_candidates
        or plan.get("selection_fallback")
        != "retain_step_20_parent_if_no_candidate_passes"
        or plan.get("original_refinement_mutated") is not False
    ):
        raise ArtifactError("browser-action continuation plan is incompatible or drifted")

    source_fields = {
        "refinement_receipt_sha256": "receipt_sha256",
        "post_sft_receipt_sha256": "post_sft_receipt_sha256",
        "refinement_plan_sha256": "plan_sha256",
        "refinement_config_sha256": "config_sha256",
        "refinement_dataset_manifest_sha256": "dataset_manifest_sha256",
        "parent_merge_provenance_sha256": "parent_merge_provenance_sha256",
        "step_20_adapter_receipt_tree_sha256": "adapter_receipt_tree_sha256",
    }
    for plan_key, source_key in source_fields.items():
        if plan_source.get(plan_key) != source.get(source_key):
            raise ArtifactError(f"continuation source binding changed: {plan_key}")
    plan_adapter = plan_source.get("step_20_adapter")
    plan_dcp = plan_source.get("step_20_dcp")
    if not isinstance(plan_adapter, dict) or not isinstance(plan_dcp, dict):
        raise ArtifactError("continuation plan lacks original step-20 identities")
    if plan_adapter.get("path") != source["adapter_identity"].get("path"):
        raise ArtifactError("continuation plan names the wrong source step-20 adapter")
    if plan_dcp.get("path") != source["source_dcp_identity"].get("path"):
        raise ArtifactError("continuation plan names the wrong source step-20 DCP")
    _same_tree(source["adapter_identity"], plan_adapter, label="source step-20 adapter")
    _same_tree(source["source_dcp_identity"], plan_dcp, label="source step-20 DCP")

    targets, data_identity = _verified_dataset(
        campaign,
        curriculum_manifest_path=curriculum_manifest,
        dataset=dataset,
        smoke_report=smoke_report,
    )
    if plan.get("data") != data_identity:
        raise ArtifactError("continuation curriculum or PRIME data binding changed")
    config = _parse_config(config_path)
    return plan, config, data_identity, targets


def _verify_config(
    campaign: Campaign,
    *,
    config: Mapping[str, Any],
    continuation: Path,
    root: Path,
    targets: list[str],
    num_gpus: int,
) -> dict[str, Any]:
    parent = (root / "selected/merged").resolve()
    dataset = (continuation.parent / "prime").resolve()
    output = (continuation / "prime_output").resolve()
    settings = campaign.campaign["refinement"]
    expected_ckpt = {
        "interval": 2,
        "resume_step": _SOURCE_STEP,
        "keep_last": 10,
        "skip_optimizer": True,
        "skip_scheduler": True,
        "skip_dataloader": True,
        "skip_progress": False,
        "weights": {
            "save_sharded": True,
            "save_format": "safetensors",
            "save_adapter_separately": True,
        },
    }
    lora = config.get("model", {}).get("lora", {})
    deployment = config.get("deployment", {})
    data = config.get("data", {})
    try:
        correct_numbers = (
            int(lora.get("rank", -1)) == 64
            and float(lora.get("alpha", -1)) == 128.0
            and float(config.get("optim", {}).get("lr", -1)) == _LEARNING_RATE
        )
    except (TypeError, ValueError):
        correct_numbers = False
    if (
        config.get("max_steps") != _FINAL_STEP
        or config.get("clean_output_dir") is not False
        or Path(str(config.get("output_dir", ""))).resolve() != output
        or Path(str(config.get("model", {}).get("name", ""))).resolve() != parent
        or config.get("model", {}).get("seq_len") != settings["sequence_length"]
        or not correct_numbers
        or lora.get("target_modules") != targets
        or deployment
        != {"type": "single_node", "num_gpus": num_gpus, "gpus_per_node": num_gpus}
        or Path(str(data.get("name", ""))).resolve() != dataset
        or data.get("batch_size") != settings["global_batch_size"]
        or data.get("seq_len") != settings["sequence_length"]
        or config.get("ckpt") != expected_ckpt
    ):
        raise ArtifactError("continuation config or PRIME restore policy drifted")
    return {
        "resume_step": _SOURCE_STEP,
        "restore_model": True,
        "restore_progress": True,
        "restore_optimizer": False,
        "restore_scheduler": False,
        "restore_dataloader": False,
        "skip_optimizer": True,
        "skip_scheduler": True,
        "skip_dataloader": True,
        "skip_progress": False,
    }


def _verify_copied_dcp(
    *, continuation: Path, plan: Mapping[str, Any], source: Mapping[str, Any]
) -> dict[str, Any]:
    destination = continuation / "prime_output/checkpoints/step_20"
    actual = _tree_identity(destination)
    planned = plan.get("copied_step_20_dcp")
    if not isinstance(planned, dict) or planned.get("path") != actual.get("path"):
        raise ArtifactError("copied step-20 DCP is absent or misplaced")
    _same_tree(actual, planned, label="copied step-20 DCP")
    _same_tree(actual, source["source_dcp_identity"], label="copied step-20 DCP")
    return actual


def _verify_adapter(
    *, adapter: Path, parent: Path, targets: list[str], update: int
) -> dict[str, Any]:
    identity = _tree_identity(adapter)
    config_path = adapter / "adapter_config.json"
    value = read_json(config_path)
    if not isinstance(value, dict):
        raise ArtifactError(f"step-{update} adapter configuration is invalid")
    declared_parent = str(value.get("base_model_name_or_path", ""))
    declared_targets = value.get("target_modules")
    expected_target_names: set[str] = set()
    for pattern in targets:
        match = re.search(r"\\\.([A-Za-z0-9_]+)\$$", pattern)
        if match is None:
            raise ArtifactError("continuation config contains an unrecognized LoRA target")
        expected_target_names.add(match.group(1))
    try:
        correct_shape = value.get("r") == 64 and float(value.get("lora_alpha", -1)) == 128.0
    except (TypeError, ValueError):
        correct_shape = False
    if (
        not declared_parent
        or Path(declared_parent).resolve() != parent
        or not correct_shape
        or not isinstance(declared_targets, list)
        # PRIME gives PEFT exact per-layer regexes at construction time.  PEFT
        # serializes their equivalent terminal module-name set in the adapter.
        or set(declared_targets) != expected_target_names
    ):
        raise ArtifactError(f"step-{update} adapter parent or LoRA shape drifted")
    weights = list(adapter.glob("adapter_model*.safetensors")) + list(
        adapter.glob("adapter_model*.bin")
    )
    if len(weights) != 1 or weights[0].stat().st_size == 0:
        raise ArtifactError(f"step-{update} adapter has missing or ambiguous weights")
    stable = adapter.parent / "STABLE"
    stable_sha256 = sha256_file(stable)
    stable_mtime = stable.stat().st_mtime_ns
    adapter_files = [item for item in adapter.rglob("*") if item.is_file()]
    if any(item.stat().st_mtime_ns > stable_mtime for item in adapter_files):
        raise ArtifactError(f"step-{update} adapter changed after STABLE")
    return {
        "update": update,
        "path": str(adapter.resolve()),
        "files": identity["files"],
        "bytes": identity["bytes"],
        "sha256": _legacy_tree_digest(adapter),
        "tree_sha256": identity["tree_sha256"],
        "adapter_config_sha256": sha256_file(config_path),
        "stable_marker_sha256": stable_sha256,
    }


def write_browser_action_continuation_receipt(
    campaign: Campaign,
    *,
    campaign_root: str | Path,
    continuation_dir: str | Path,
    artifact_source_git_sha: str | None = None,
    execution_source_git_sha: str | None = None,
) -> dict[str, Any]:
    """Verify and attest the four completed continuation adapters."""

    root = Path(campaign_root).resolve()
    continuation = Path(continuation_dir).resolve()
    artifact_source = _git_sha(
        artifact_source_git_sha or os.environ.get("HPT_ARTIFACT_SOURCE_GIT_SHA"),
        label="artifact source",
    )
    execution_source = _git_sha(
        execution_source_git_sha or os.environ.get("HPT_EXECUTION_SOURCE_GIT_SHA"),
        label="execution source",
    )
    source_identities = _verify_source_identities(
        root=root,
        continuation=continuation,
        campaign_digest=campaign.digest,
        artifact_source=artifact_source,
        execution_source=execution_source,
    )
    source_before = _verified_source(campaign, root)
    plan, config, data_identity, targets = _verify_plan(
        campaign,
        root=root,
        continuation=continuation,
        source=source_before,
    )
    restore_policy = _verify_config(
        campaign,
        config=config,
        continuation=continuation,
        root=root,
        targets=targets,
        num_gpus=int(plan["training"]["num_gpus"]),
    )
    copied_dcp = _verify_copied_dcp(
        continuation=continuation, plan=plan, source=source_before
    )
    parent = (root / "selected/merged").resolve()
    checkpoints = [
        _verify_adapter(
            adapter=(
                continuation / f"prime_output/weights/step_{step}/lora_adapters"
            ),
            parent=parent,
            targets=targets,
            update=step,
        )
        for step in _CHECKPOINT_STEPS
    ]

    # Re-read both original source trees after inspecting every output.  This
    # catches accidental in-place resumes as well as mutations during receipt
    # construction.  The plan supplies the earlier, independently published
    # identity against which both observations are checked.
    source_after = _verified_source(campaign, root)
    _same_tree(
        source_before["adapter_identity"],
        source_after["adapter_identity"],
        label="original step-20 adapter",
    )
    _same_tree(
        source_before["source_dcp_identity"],
        source_after["source_dcp_identity"],
        label="original step-20 DCP",
    )

    plan_path = continuation / "plan.json"
    config_path = continuation / "browser_action_continuation.toml"
    curriculum_manifest = continuation.parent / "curriculum/manifest.json"
    prime_manifest = continuation.parent / "prime/manifest.json"
    receipt = {
        "schema": CONTINUATION_RECEIPT_SCHEMA,
        "status": "ok",
        "stage": "browser_action_continuation",
        "campaign_digest": campaign.digest,
        **source_identities,
        "parent_model": str(parent),
        "parent_merge_provenance_sha256": source_before[
            "parent_merge_provenance_sha256"
        ],
        "plan_sha256": sha256_file(plan_path),
        "config_sha256": sha256_file(config_path),
        "curriculum_manifest_sha256": sha256_file(curriculum_manifest),
        "curriculum_data_sha256": data_identity["curriculum_data_sha256"],
        "prime_manifest_sha256": sha256_file(prime_manifest),
        "prime_parquet_sha256": data_identity["prime_parquet_sha256"],
        "optimizer_updates": _FINAL_STEP,
        "new_optimizer_updates": _FINAL_STEP - _SOURCE_STEP,
        "checkpoint_updates": _CHECKPOINT_STEPS,
        "restore_policy": restore_policy,
        "source": {
            "campaign_root": str(root),
            "refinement_receipt_sha256": source_before["receipt_sha256"],
            "post_sft_receipt_sha256": source_before["post_sft_receipt_sha256"],
            "refinement_plan_sha256": source_before["plan_sha256"],
            "refinement_config_sha256": source_before["config_sha256"],
            "refinement_dataset_manifest_sha256": source_before[
                "dataset_manifest_sha256"
            ],
            "step_20_adapter": source_before["adapter_identity"],
            "step_20_adapter_receipt_tree_sha256": source_before[
                "adapter_receipt_tree_sha256"
            ],
            "step_20_dcp": source_before["source_dcp_identity"],
            "verified_unchanged_before_and_after": True,
        },
        "copied_step_20_dcp": copied_dcp,
        "checkpoints": checkpoints,
    }
    publish_json(continuation / "training_receipt.json", receipt)
    return receipt
