"""Prepare a fail-closed browser-action SFT continuation from refinement step 20.

The continuation is deliberately a new artifact tree.  It copies the complete
PRIME distributed checkpoint, verifies the copy byte for byte, and configures
PRIME to restore model/progress state while starting fresh optimizer, scheduler,
and dataloader state.  The original refinement output is never written.
"""

from __future__ import annotations

import math
import os
import shutil
import stat
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .artifacts import (
    ArtifactError,
    canonical_json,
    publish_bytes,
    publish_json,
    read_json,
    sha256_bytes,
    sha256_file,
)
from .browser_action_curriculum import CURRICULUM_SCHEMA
from .config import Campaign
from .prime_data import PRIME_DATASET_SCHEMA
from .train_configs import _array, _quoted, _smoke_targets

CONTINUATION_PLAN_SCHEMA = "caveat-27b.browser-action-continuation-plan.v1"

_SOURCE_STEP = 20
_FINAL_STEP = 28
_CHECKPOINT_STEPS = [22, 24, 26, 28]
_LEARNING_RATE = 2.0e-6


def _tree_identity(path: str | Path) -> dict[str, Any]:
    """Return a content identity while rejecting symlinks and special files."""

    root = Path(path).absolute()
    if root.is_symlink() or not root.is_dir():
        raise ArtifactError(f"checkpoint tree is absent or is a symlink: {root}")
    entries: dict[str, dict[str, Any]] = {}
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in directories:
            candidate = current_path / name
            metadata = candidate.lstat()
            if not stat.S_ISDIR(metadata.st_mode) or candidate.is_symlink():
                raise ArtifactError(f"checkpoint contains an unsafe directory: {candidate}")
        for name in files:
            candidate = current_path / name
            metadata = candidate.lstat()
            if not stat.S_ISREG(metadata.st_mode) or candidate.is_symlink():
                raise ArtifactError(f"checkpoint contains an unsafe file: {candidate}")
            entries[candidate.relative_to(root).as_posix()] = {
                "size": metadata.st_size,
                "sha256": sha256_file(candidate),
            }
    if not entries:
        raise ArtifactError(f"checkpoint tree is empty: {root}")
    return {
        "path": str(root.resolve()),
        "files": len(entries),
        "bytes": sum(int(row["size"]) for row in entries.values()),
        "tree_sha256": sha256_bytes(canonical_json(entries).encode()),
    }


def _checkpoint_descriptor(receipt: Mapping[str, Any], step: int) -> Mapping[str, Any]:
    values = receipt.get("checkpoints")
    if not isinstance(values, list):
        raise ArtifactError("refinement receipt has no checkpoint inventory")
    matches = [row for row in values if isinstance(row, Mapping) and row.get("update") == step]
    if len(matches) != 1:
        raise ArtifactError(f"refinement receipt does not attest exactly one step-{step} adapter")
    return matches[0]


def _verified_source(campaign: Campaign, campaign_root: Path) -> dict[str, Any]:
    receipt_path = campaign_root / "refinement/training_receipt.json"
    receipt = read_json(receipt_path)
    if (
        not isinstance(receipt, dict)
        or receipt.get("schema") != "caveat-27b.refinement-training-receipt.v1"
        or receipt.get("status") != "ok"
        or receipt.get("campaign_digest") != campaign.digest
        or receipt.get("optimizer_updates") != _SOURCE_STEP
    ):
        raise ArtifactError("continuation requires the exact successful step-20 receipt")

    post_path = campaign_root / "post_sft_receipt.json"
    post = read_json(post_path)
    if (
        not isinstance(post, dict)
        or post.get("schema") != "caveat-27b.post-sft-receipt.v1"
        or post.get("status") != "ok"
        or post.get("campaign_digest") != campaign.digest
        or receipt.get("post_sft_receipt_sha256") != sha256_file(post_path)
    ):
        raise ArtifactError("post-SFT receipt is incompatible or changed")

    parent = (campaign_root / "selected/merged").resolve()
    provenance_path = parent / "merge_provenance.json"
    provenance = read_json(provenance_path)
    if (
        Path(str(receipt.get("parent_model", ""))).resolve() != parent
        or receipt.get("parent_merge_provenance_sha256") != sha256_file(provenance_path)
        or post.get("merge_provenance_sha256") != sha256_file(provenance_path)
        or not isinstance(provenance, dict)
        or provenance.get("status") != "ok"
    ):
        raise ArtifactError("selected SFT parent provenance is incompatible or changed")

    plan_path = campaign_root / "refinement/config/plan.json"
    config_path = campaign_root / "refinement/config/refinement.toml"
    dataset_manifest_path = campaign_root / "refinement/prime/manifest.json"
    plan = read_json(plan_path)
    if (
        not isinstance(plan, dict)
        or plan.get("schema") != "caveat-27b.train-plan.v1"
        or plan.get("stage") != "refinement"
        or plan.get("candidate") is not None
        or plan.get("campaign_digest") != campaign.digest
        or plan.get("optimizer_updates") != _SOURCE_STEP
        or Path(str(plan.get("model", ""))).resolve() != parent
        or plan.get("config_sha256") != sha256_file(config_path)
        or receipt.get("plan_sha256") != sha256_file(plan_path)
        or receipt.get("config_sha256") != sha256_file(config_path)
        or post.get("refinement_config_sha256") != sha256_file(config_path)
        or plan.get("dataset_manifest_sha256") != sha256_file(dataset_manifest_path)
        or receipt.get("dataset_manifest_sha256") != sha256_file(dataset_manifest_path)
        or post.get("prime_manifest_sha256") != sha256_file(dataset_manifest_path)
    ):
        raise ArtifactError("original refinement plan or one of its bound inputs changed")

    try:
        source_config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ArtifactError("original refinement configuration is invalid") from exc
    expected_source_output = (campaign_root / "refinement/config/prime_output").resolve()
    if (
        source_config.get("max_steps") != _SOURCE_STEP
        or Path(str(source_config.get("output_dir", ""))).resolve() != expected_source_output
        or Path(str(source_config.get("model", {}).get("name", ""))).resolve() != parent
        or source_config.get("ckpt", {}).get("resume_step") != -1
        or source_config.get("model", {}).get("lora", {}).get("rank") != 64
        or float(source_config.get("model", {}).get("lora", {}).get("alpha", -1)) != 128.0
    ):
        raise ArtifactError("original refinement configuration has drifted")

    adapter = expected_source_output / "weights/step_20/lora_adapters"
    descriptor = _checkpoint_descriptor(receipt, _SOURCE_STEP)
    adapter_identity = _tree_identity(adapter)
    stable_path = adapter.parent / "STABLE"
    if (
        Path(str(descriptor.get("path", ""))).resolve() != adapter
        or descriptor.get("files") != adapter_identity["files"]
        or descriptor.get("sha256") != _legacy_tree_digest(adapter)
        or descriptor.get("stable_marker_sha256") != sha256_file(stable_path)
    ):
        raise ArtifactError("attested step-20 adapter bytes changed")

    source_dcp = expected_source_output / "checkpoints/step_20"
    dcp_identity = _tree_identity(source_dcp)
    return {
        "receipt_path": receipt_path,
        "receipt_sha256": sha256_file(receipt_path),
        "post_sft_receipt_sha256": sha256_file(post_path),
        "plan_sha256": sha256_file(plan_path),
        "config_sha256": sha256_file(config_path),
        "dataset_manifest_sha256": sha256_file(dataset_manifest_path),
        "parent": parent,
        "parent_merge_provenance_sha256": sha256_file(provenance_path),
        "adapter_identity": adapter_identity,
        "adapter_receipt_tree_sha256": str(descriptor["sha256"]),
        "source_dcp": source_dcp,
        "source_dcp_identity": dcp_identity,
    }


def _legacy_tree_digest(path: Path) -> str:
    """Match the tree hash stored by the original refinement receipt."""

    from .artifacts import tree_digest

    files = [item for item in path.rglob("*") if item.is_file()]
    return tree_digest(files, path)


def _verified_dataset(
    campaign: Campaign,
    *,
    curriculum_manifest_path: Path,
    dataset: Path,
    smoke_report: Path,
) -> tuple[list[str], dict[str, Any]]:
    curriculum = read_json(curriculum_manifest_path)
    if not isinstance(curriculum, dict):
        raise ArtifactError("browser-action curriculum manifest is invalid")
    curriculum_body = dict(curriculum)
    recorded_body_hash = curriculum_body.pop("manifest_body_sha256", None)
    if (
        curriculum.get("schema") != CURRICULUM_SCHEMA
        or curriculum.get("stage") != "refinement"
        or curriculum.get("campaign_digest") != campaign.digest
        or curriculum.get("assistant_wire_format") != "browser-use AgentOutput.action JSON"
        or curriculum.get("native_function_call_targets") != 0
        or curriculum.get("heldout_amazon_scenarios_present") is not False
        or recorded_body_hash != sha256_bytes(canonical_json(curriculum_body).encode())
    ):
        raise ArtifactError("browser-action curriculum manifest is incompatible or drifted")
    source_path = curriculum_manifest_path.parent / str(curriculum.get("output", {}).get("path"))
    if curriculum.get("output", {}).get("sha256") != sha256_file(source_path):
        raise ArtifactError("browser-action curriculum data changed after publication")

    manifest_path = dataset / "manifest.json"
    parquet_path = dataset / "train.parquet"
    manifest = read_json(manifest_path)
    settings = campaign.campaign["refinement"]
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema") != PRIME_DATASET_SCHEMA
        or manifest.get("stage") != "refinement"
        or manifest.get("candidate") is not None
        or manifest.get("campaign_digest") != campaign.digest
        or manifest.get("model_revision") != campaign.model["revision"]
        or Path(str(manifest.get("source", ""))).resolve() != source_path.resolve()
        or manifest.get("source_sha256") != sha256_file(source_path)
        or manifest.get("source_manifest_sha256") != sha256_file(curriculum_manifest_path)
        or manifest.get("parquet_sha256") != sha256_file(parquet_path)
        or manifest.get("row_count") != curriculum.get("output", {}).get("rows")
        or manifest.get("token_audit", {}).get("seq_len") != settings["sequence_length"]
        or manifest.get("token_audit", {}).get("global_batch_size")
        != settings["global_batch_size"]
    ):
        raise ArtifactError("browser-action PRIME dataset is incompatible or drifted")

    _snapshot, targets, revision = _smoke_targets(smoke_report, campaign)
    if revision and revision != campaign.model["revision"]:
        raise ArtifactError("smoke revision differs from the pinned model revision")
    return targets, {
        "curriculum_manifest_sha256": sha256_file(curriculum_manifest_path),
        "curriculum_data_sha256": sha256_file(source_path),
        "prime_manifest_sha256": sha256_file(manifest_path),
        "prime_parquet_sha256": sha256_file(parquet_path),
        "smoke_report_sha256": sha256_file(smoke_report),
        "rows": manifest["row_count"],
    }


def _continuation_toml(
    *,
    model: Path,
    dataset: Path,
    output: Path,
    target_patterns: list[str],
    settings: Mapping[str, Any],
    seed: int,
    num_gpus: int,
) -> bytes:
    continuation_updates = _FINAL_STEP - _SOURCE_STEP
    warmup_steps = min(
        continuation_updates - 1,
        max(1, math.ceil(continuation_updates * float(settings["warmup_fraction"]))),
    )
    lines = [
        "# PRIME-RL 0.7.0; generated by caveat-27b",
        "# stage = browser_action_continuation",
        f"# predeclared_candidate_updates = {_CHECKPOINT_STEPS}",
        f"max_steps = {_FINAL_STEP}",
        f"output_dir = {_quoted(output)}",
        "clean_output_dir = false",
        'matmul_precision = "high"',
        'loss_impl = "liger_fused"',
        "",
        "[env_vars]",
        'FLA_TILELANG = "0"',
        'WANDB_MODE = "disabled"',
        "",
        "[deployment]",
        'type = "single_node"',
        f"num_gpus = {num_gpus}",
        f"gpus_per_node = {num_gpus}",
        "",
        "[model]",
        f"name = {_quoted(model)}",
        f"seq_len = {int(settings['sequence_length'])}",
        'impl = "hf"',
        'attn = "flash_attention_2"',
        'optimization_dtype = "bfloat16"',
        'reduce_dtype = "bfloat16"',
        "cp = 2",
        'cp_style = "ulysses"',
        "",
        "[model.ac]",
        'mode = "full"',
        "freq = 1",
        "",
        "[model.lora]",
        f"rank = {int(settings['lora_rank'])}",
        f"alpha = {float(settings['lora_alpha'])}",
        f"dropout = {float(settings['lora_dropout'])}",
        f"target_modules = {_array(target_patterns)}",
        "modules_to_save = []",
        "",
        "[renderer]",
        'name = "qwen3.5"',
        "enable_thinking = true",
        "",
        "[data]",
        'type = "sft"',
        f"name = {_quoted(dataset)}",
        f"batch_size = {int(settings['global_batch_size'])}",
        f"seq_len = {int(settings['sequence_length'])}",
        "micro_batch_size = 1",
        'pack_function = "cat"',
        "shuffle = true",
        f"seed = {seed}",
        "",
        "[data.loss_mask]",
        "system = false",
        "user = false",
        "assistant = true",
        "tool = false",
        "",
        "[optim]",
        'type = "adamw"',
        f"lr = {_LEARNING_RATE}",
        "weight_decay = 0.01",
        f"max_norm = {float(settings.get('maximum_gradient_norm', 1.0))}",
        "",
        "[scheduler]",
        'type = "cosine"',
        f"warmup_steps = {warmup_steps}",
        "min_lr = 0.0",
        "",
        "[ckpt]",
        "interval = 2",
        f"resume_step = {_SOURCE_STEP}",
        "keep_last = 10",
        "skip_optimizer = true",
        "skip_scheduler = true",
        "skip_dataloader = true",
        "skip_progress = false",
        "",
        "[ckpt.weights]",
        "save_sharded = true",
        'save_format = "safetensors"',
        "save_adapter_separately = true",
    ]
    payload = ("\n".join(lines).rstrip() + "\n").encode()
    try:
        parsed = tomllib.loads(payload.decode())
    except tomllib.TOMLDecodeError as exc:  # pragma: no cover - construction invariant
        raise ArtifactError("generated browser-action continuation TOML is invalid") from exc
    expected_restore = {
        "resume_step": _SOURCE_STEP,
        "skip_optimizer": True,
        "skip_scheduler": True,
        "skip_dataloader": True,
        "skip_progress": False,
    }
    if any(parsed["ckpt"].get(key) != value for key, value in expected_restore.items()):
        raise AssertionError("continuation restore policy drifted")
    return payload


def prepare_browser_action_continuation(
    campaign: Campaign,
    *,
    campaign_root: str | Path,
    curriculum_manifest: str | Path,
    dataset_dir: str | Path,
    smoke_report: str | Path,
    output_dir: str | Path,
    num_gpus: int = 4,
) -> dict[str, Any]:
    """Copy step 20 and emit a hash-bound eight-update continuation plan."""

    if num_gpus not in {4, 8}:
        raise ArtifactError("reviewed continuation topology requires four or eight GPUs")
    root = Path(campaign_root).resolve()
    curriculum_manifest_path = Path(curriculum_manifest).resolve()
    dataset = Path(dataset_dir).resolve()
    smoke_report_path = Path(smoke_report).resolve()
    requested_output = Path(output_dir).absolute()
    if requested_output.is_symlink():
        raise ArtifactError(f"continuation output must not be a symlink: {requested_output}")
    output = requested_output.resolve()
    if output.exists() and not output.is_dir():
        raise ArtifactError(f"continuation output must be a new empty directory: {output}")
    if output.exists() and any(output.iterdir()):
        raise ArtifactError(f"continuation output must be a new empty directory: {output}")
    if output == root:
        raise ArtifactError("continuation output cannot be the original campaign root")
    # A child of the campaign root is permitted, but it must not overlap the
    # original refinement output in either direction.
    original = (root / "refinement/config/prime_output").resolve()
    if output == original or original in output.parents or output in original.parents:
        raise ArtifactError("continuation output overlaps the original refinement output")

    source = _verified_source(campaign, root)
    targets, data_identity = _verified_dataset(
        campaign,
        curriculum_manifest_path=curriculum_manifest_path,
        dataset=dataset,
        smoke_report=smoke_report_path,
    )

    destination_dcp = output / "prime_output/checkpoints/step_20"
    destination_dcp.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source["source_dcp"], destination_dcp, copy_function=shutil.copy2)
    copied_identity = _tree_identity(destination_dcp)
    source_after_copy = _tree_identity(source["source_dcp"])
    if (
        source_after_copy["tree_sha256"] != source["source_dcp_identity"]["tree_sha256"]
        or copied_identity["tree_sha256"] != source["source_dcp_identity"]["tree_sha256"]
        or copied_identity["files"] != source["source_dcp_identity"]["files"]
        or copied_identity["bytes"] != source["source_dcp_identity"]["bytes"]
    ):
        raise ArtifactError("step-20 checkpoint changed during copy or its copy differs")

    config_payload = _continuation_toml(
        model=source["parent"],
        dataset=dataset,
        output=(output / "prime_output").resolve(),
        target_patterns=targets,
        settings=campaign.campaign["refinement"],
        seed=int(campaign.campaign["seed"]) + 3,
        num_gpus=num_gpus,
    )
    config_path = publish_bytes(output / "browser_action_continuation.toml", config_payload)
    candidates = [
        {
            "update": step,
            "adapter": str(
                (output / f"prime_output/weights/step_{step}/lora_adapters").resolve()
            ),
        }
        for step in _CHECKPOINT_STEPS
    ]
    plan = {
        "schema": CONTINUATION_PLAN_SCHEMA,
        "stage": "browser_action_continuation",
        "status": "prepared",
        "campaign_digest": campaign.digest,
        "model_revision": campaign.model["revision"],
        "parent_model": str(source["parent"]),
        "source": {
            "campaign_root": str(root),
            "refinement_receipt_sha256": source["receipt_sha256"],
            "post_sft_receipt_sha256": source["post_sft_receipt_sha256"],
            "refinement_plan_sha256": source["plan_sha256"],
            "refinement_config_sha256": source["config_sha256"],
            "refinement_dataset_manifest_sha256": source["dataset_manifest_sha256"],
            "parent_merge_provenance_sha256": source["parent_merge_provenance_sha256"],
            "step_20_adapter": source["adapter_identity"],
            "step_20_adapter_receipt_tree_sha256": source[
                "adapter_receipt_tree_sha256"
            ],
            "step_20_dcp": source["source_dcp_identity"],
        },
        "copied_step_20_dcp": copied_identity,
        "data": data_identity,
        "dataset": str(dataset),
        "config": str(config_path),
        "config_sha256": sha256_file(config_path),
        "training": {
            "num_gpus": num_gpus,
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
        },
        "candidates": candidates,
        "selection_fallback": "retain_step_20_parent_if_no_candidate_passes",
        "original_refinement_mutated": False,
    }
    plan_path = publish_json(output / "plan.json", plan)
    result = dict(plan)
    result["plan_sha256"] = sha256_file(plan_path)
    return result
