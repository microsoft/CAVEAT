"""Create and validate the exact-LoRA pair for fixed-v5 correction."""

from __future__ import annotations

import shutil
import tempfile
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
from .browser_action_finalization import (
    _published_identity,
    _structural_attestation,
    _tokenizer_attestation,
)
from .browser_action_fixed_v5_gate import (
    FIXED_V5_GATE_MANIFEST_SCHEMA,
    FIXED_V5_GATE_POLICY_SCHEMA,
    FIXED_V5_TRAINING_RECEIPT_SCHEMA,
    _bound_record,
    _training_inventory,
    run_fixed_v5_gate,
)
from .config import Campaign
from .exact_lora import (
    EXACT_LORA_MANIFEST_SCHEMA,
    _verified_raw_base,
    component_identity,
    create_zero_adapter,
    exact_lora_composite_sha256,
)

FIXED_V5_FINALIZATION_RECEIPT_SCHEMA = (
    "caveat-27b.browser-action-fixed-v5-exact-lora-receipt.v1"
)


def _same_path(value: Any, expected: Path, *, label: str) -> None:
    if not isinstance(value, str) or Path(value).resolve() != expected:
        raise ArtifactError(f"fixed-v5 {label} path changed")


def _fixed_stage(root: Path, receipt: Path, gate: Path) -> tuple[Path, str]:
    try:
        relative = receipt.relative_to(root)
    except ValueError as exc:
        raise ArtifactError("fixed-v5 receipt is outside the campaign") from exc
    parts = relative.parts
    if (
        len(parts) != 4
        or parts[0] != "browser_action_fixed_v5"
        or len(parts[1]) != 40
        or any(character not in "0123456789abcdef" for character in parts[1])
        or parts[2:] != ("training", "training_receipt.json")
    ):
        raise ArtifactError("fixed-v5 receipt has an invalid stage layout")
    stage = (root / parts[0] / parts[1]).resolve()
    if (
        gate.name != "gate_manifest.json"
        or gate.parent.name != "procedural_gate"
        or gate.parent.parent.resolve() != stage
    ):
        raise ArtifactError("fixed-v5 gate is outside its correction stage")
    return stage, parts[1]


def _gate_descriptor(gate_path: Path) -> dict[str, Any]:
    gate = _bound_record(
        gate_path,
        schema=FIXED_V5_GATE_MANIFEST_SCHEMA,
        hash_field="manifest_body_sha256",
    )
    policy_path = Path(str(gate.get("gate_policy", ""))).resolve()
    expected_policy = (gate_path.parent / "gate_policy.json").resolve()
    expected_evidence = (gate_path.parent / "raw_evidence.jsonl").resolve()
    policy = _bound_record(
        policy_path,
        schema=FIXED_V5_GATE_POLICY_SCHEMA,
        hash_field="policy_body_sha256",
    )
    evidence = gate.get("raw_evidence")
    candidate = gate.get("candidate")
    if (
        gate.get("status") != "complete"
        or gate.get("selection_performed") is not False
        or gate.get("fixed_candidate") != "step26"
        or gate.get("caveat_shop_data_used") is not False
        or gate.get("passed") is not True
        or not isinstance(gate.get("gate_checks"), Mapping)
        or not gate["gate_checks"]
        or not all(value is True for value in gate["gate_checks"].values())
        or not isinstance(candidate, Mapping)
        or candidate.get("name") != "step26-fixed-v5"
        or candidate.get("update") != 26
        or not isinstance(evidence, Mapping)
        or policy_path != expected_policy
        or Path(str(evidence.get("path", ""))).resolve() != expected_evidence
        or evidence.get("rows") != 192
        or evidence.get("baseline_rows") != 96
        or evidence.get("candidate_rows") != 96
        or sha256_file(Path(str(evidence.get("path", "")))) != evidence.get("sha256")
        or sha256_file(policy_path) != gate.get("gate_policy_sha256")
        or policy.get("status") != "frozen_before_candidate_inference"
        or policy.get("caveat_shop_data_used") is not False
        or policy.get("selection_performed") is not False
        or gate.get("gate_policy_body_sha256") != policy.get("policy_body_sha256")
    ):
        raise ArtifactError("fixed-v5 procedural gate did not pass its frozen checks")
    return gate


def _pair_sha256(base: str, trained: str) -> str:
    return sha256_bytes(
        canonical_json(
            {"schema": EXACT_LORA_MANIFEST_SCHEMA, "base": base, "trained": trained}
        ).encode()
    )


def validate_fixed_v5_serving_manifest(
    manifest_path: str | Path, *, arm: str
) -> dict[str, Any]:
    """Re-hash both fixed-v5 arms and all immutable gate bindings."""

    path = Path(manifest_path).resolve()
    if arm not in {"base", "trained"}:
        raise ArtifactError("fixed-v5 serving arm must be base or trained")
    if path.name != "exact_lora_manifest.json" or path.parent.name != "final":
        raise ArtifactError("fixed-v5 exact-LoRA manifest is outside its final directory")
    stage = path.parent.parent
    artifact_sha = stage.name
    if (
        stage.parent.name != "browser_action_fixed_v5"
        or len(artifact_sha) != 40
        or any(character not in "0123456789abcdef" for character in artifact_sha)
    ):
        raise ArtifactError("fixed-v5 manifest is outside its source-SHA stage")
    manifest = read_json(path)
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema") != EXACT_LORA_MANIFEST_SCHEMA
        or manifest.get("status") != "ok"
        # Keep the evaluator's generic correction-stage contract unchanged.
        or manifest.get("stage") != "browser_action_correction"
        or manifest.get("correction_method") != "fixed_v5_one_candidate_gate"
        or manifest.get("artifact_source_git_sha") != artifact_sha
        or manifest.get("serving_mode") != "exact_peft_lora"
        or manifest.get("final_model_directory_published") is not False
        or manifest.get("bf16_weights_merged") is not False
        or (path.parent / "model").exists()
    ):
        raise ArtifactError("fixed-v5 exact-LoRA serving manifest is incompatible")
    receipt_path = path.parent / "exact_lora_manifest_receipt.json"
    receipt = _bound_record(
        receipt_path,
        schema=FIXED_V5_FINALIZATION_RECEIPT_SCHEMA,
        hash_field="receipt_body_sha256",
    )
    if (
        receipt.get("status") != "ok"
        or receipt.get("manifest_path") != str(path)
        or receipt.get("manifest_sha256") != sha256_file(path)
        or receipt.get("composite_pair_sha256") != manifest.get("composite_pair_sha256")
        or receipt.get("selected_update") != 26
    ):
        raise ArtifactError("fixed-v5 exact-LoRA receipt is incompatible")

    shared = manifest.get("shared_tokenizer")
    descriptors = manifest.get("arms")
    if (
        not isinstance(shared, Mapping)
        or not isinstance(descriptors, Mapping)
        or set(descriptors) != {"base", "trained"}
    ):
        raise ArtifactError("fixed-v5 exact-LoRA arm or tokenizer descriptors are absent")
    tokenizer_path = Path(str(shared.get("path", ""))).resolve()
    if sha256_file(tokenizer_path / "tokenizer.json") != shared.get("tokenizer_json_sha256"):
        raise ArtifactError("fixed-v5 tokenizer bytes changed")
    validated: dict[str, dict[str, str]] = {}
    for name in ("base", "trained"):
        descriptor = descriptors[name]
        if not isinstance(descriptor, Mapping):
            raise ArtifactError(f"fixed-v5 {name} descriptor is malformed")
        parent = Path(str(descriptor.get("parent", {}).get("path", ""))).resolve()
        adapter = Path(str(descriptor.get("adapter", {}).get("path", ""))).resolve()
        if (
            component_identity(parent) != descriptor.get("parent")
            or component_identity(adapter) != descriptor.get("adapter")
            or sha256_file(adapter / "adapter_config.json")
            != descriptor.get("adapter_config_sha256")
        ):
            raise ArtifactError(f"fixed-v5 {name} component bytes changed")
        composite = exact_lora_composite_sha256(
            parent_tree_sha256=str(descriptor["parent"]["tree_sha256"]),
            adapter_tree_sha256=str(descriptor["adapter"]["tree_sha256"]),
            adapter_config_sha256=str(descriptor["adapter_config_sha256"]),
            tokenizer_json_sha256=str(shared["tokenizer_json_sha256"]),
            chat_template_sha256=str(shared["chat_template_sha256"]),
            dtype=str(manifest.get("inference", {}).get("dtype", "")),
        )
        expected_name = (
            "qwen35-27b-base-exact-lora"
            if name == "base"
            else f"caveat-27b-fixed-v5-{artifact_sha[:12]}-exact-lora"
        )
        if (
            descriptor.get("composite_sha256") != composite
            or descriptor.get("served_model_name") != expected_name
        ):
            raise ArtifactError(f"fixed-v5 {name} serving identity changed")
        validated[name] = {
            "parent": str(parent),
            "adapter": str(adapter),
            "served_model_name": expected_name,
            "composite_sha256": composite,
        }
    if (
        manifest.get("composite_pair_sha256")
        != _pair_sha256(
            validated["base"]["composite_sha256"],
            validated["trained"]["composite_sha256"],
        )
        or validated["base"]["composite_sha256"]
        == validated["trained"]["composite_sha256"]
    ):
        raise ArtifactError("fixed-v5 composite-pair identity changed or aliases")

    selected = manifest.get("selected_checkpoint")
    if (
        not isinstance(selected, Mapping)
        or selected.get("name") != "step26"
        or selected.get("update") != 26
        or selected.get("selection_performed") is not False
        or Path(str(selected.get("adapter", ""))).resolve()
        != Path(validated["trained"]["adapter"])
        or selected.get("adapter_tree_sha256")
        != descriptors["trained"]["adapter"]["tree_sha256"]
    ):
        raise ArtifactError("fixed-v5 fixed-candidate binding changed")
    receipts = manifest.get("receipts")
    if not isinstance(receipts, Mapping):
        raise ArtifactError("fixed-v5 source receipts are absent")
    training_descriptor = receipts.get("browser_action_fixed_v5_training")
    gate_descriptor = receipts.get("browser_action_fixed_v5_gate")
    training_path = stage / "training/training_receipt.json"
    gate_path = stage / "procedural_gate/gate_manifest.json"
    for descriptor, expected, schema, label in (
        (
            training_descriptor,
            training_path,
            FIXED_V5_TRAINING_RECEIPT_SCHEMA,
            "training",
        ),
        (gate_descriptor, gate_path, FIXED_V5_GATE_MANIFEST_SCHEMA, "gate"),
    ):
        if (
            not isinstance(descriptor, Mapping)
            or descriptor.get("schema") != schema
            or descriptor.get("status") not in {"ok", "complete"}
        ):
            raise ArtifactError(f"fixed-v5 {label} receipt descriptor is invalid")
        _same_path(descriptor.get("path"), expected, label=label)
        if descriptor.get("sha256") != sha256_file(expected):
            raise ArtifactError(f"fixed-v5 {label} receipt bytes changed")
    training = _bound_record(
        training_path,
        schema=FIXED_V5_TRAINING_RECEIPT_SCHEMA,
        hash_field="receipt_body_sha256",
    )
    gate = _gate_descriptor(gate_path)
    if (
        training.get("candidate", {}).get("tree_sha256")
        != selected.get("adapter_tree_sha256")
        or gate.get("candidate", {}).get("tree_sha256")
        != selected.get("adapter_tree_sha256")
        or gate.get("training_receipt_sha256") != sha256_file(training_path)
        or receipt.get("training_receipt_sha256") != sha256_file(training_path)
        or receipt.get("gate_manifest_sha256") != sha256_file(gate_path)
    ):
        raise ArtifactError("fixed-v5 training/gate/final binding changed")
    structural = manifest.get("structural_attestation", {})
    if (
        structural.get("status") != "ok"
        or structural.get("zero_noop", {}).get("passed") is not True
        or structural.get("trained_nonzero", {}).get("passed") is not True
    ):
        raise ArtifactError("fixed-v5 structural attestation is incomplete")
    requested = validated[arm]
    return {
        **requested,
        "tokenizer": str(tokenizer_path),
        "manifest_path": str(path),
        "manifest_sha256": sha256_file(path),
        "manifest_receipt_path": str(receipt_path),
        "manifest_receipt_sha256": sha256_file(receipt_path),
        "campaign_digest": str(manifest.get("campaign_digest")),
        "artifact_source_git_sha": artifact_sha,
        "selected_checkpoint": dict(selected),
        # These legacy result keys are opaque source bindings in the committed
        # evaluator; their records explicitly retain the fixed-v5 schemas.
        "continuation_receipt": dict(training_descriptor),
        "selection_receipt": dict(gate_descriptor),
    }


def finalize_fixed_v5(
    campaign: Campaign,
    *,
    campaign_root: str | Path,
    training_receipt: str | Path,
    gate_manifest: str | Path,
    baseline_selection_dir: str | Path,
    raw_base: str | Path,
) -> dict[str, Any]:
    """Publish one create-only exact-LoRA pair after the fixed gate passes."""

    root = Path(campaign_root).resolve()
    receipt_path = Path(training_receipt).resolve()
    gate_path = Path(gate_manifest).resolve()
    raw = Path(raw_base).resolve()
    stage, artifact_sha = _fixed_stage(root, receipt_path, gate_path)
    output = stage / "final"
    if output.exists() or output.is_symlink():
        # Existing outputs are never overwritten; fully validate exact reuse.
        validate_fixed_v5_serving_manifest(output / "exact_lora_manifest.json", arm="base")
        validate_fixed_v5_serving_manifest(output / "exact_lora_manifest.json", arm="trained")
        return read_json(output / "exact_lora_manifest.json")
    gate = run_fixed_v5_gate(
        campaign,
        campaign_root=root,
        training_receipt=receipt_path,
        baseline_selection_dir=baseline_selection_dir,
        output_dir=gate_path.parent,
        concurrency=1,
        num_gpus=int(read_json(gate_path.parent / "gate_policy.json")["num_gpus"]),
    )
    if gate.get("passed") is not True:
        raise ArtifactError("fixed-v5 procedural gate did not pass")
    parent, candidate, training = _training_inventory(campaign, root, receipt_path)
    if gate.get("candidate") != candidate:
        raise ArtifactError("fixed-v5 gate candidate differs from training receipt")
    smoke_path = _verified_raw_base(campaign, root, raw)
    selected_adapter = Path(candidate["path"]).resolve()
    raw_before = component_identity(raw)
    parent_before = component_identity(parent)
    selected_before = component_identity(selected_adapter)
    if selected_before["tree_sha256"] == gate.get("baseline_provenance", {}).get(
        "candidate_tree_sha256"
    ):
        raise ArtifactError("fixed-v5 candidate aliases its baseline")
    tokenizer = _tokenizer_attestation(raw, parent)
    semantic = tokenizer["semantic"]

    staging = Path(tempfile.mkdtemp(prefix=".final.", dir=stage))
    try:
        staged_zero = staging / "base_zero_adapter"
        zero_attestation = create_zero_adapter(selected_adapter, raw, staged_zero)
        zero_attestation = {
            **zero_attestation,
            "zero_adapter": str((output / "base_zero_adapter").resolve()),
        }
        structural = _structural_attestation(selected_adapter, zero_attestation)
        # Re-run all mutable source checks after deriving the zero adapter.
        repeated_gate = run_fixed_v5_gate(
            campaign,
            campaign_root=root,
            training_receipt=receipt_path,
            baseline_selection_dir=baseline_selection_dir,
            output_dir=gate_path.parent,
            concurrency=1,
            num_gpus=int(read_json(gate_path.parent / "gate_policy.json")["num_gpus"]),
        )
        repeated_parent, repeated_candidate, repeated_training = _training_inventory(
            campaign, root, receipt_path
        )
        if (
            repeated_gate != gate
            or repeated_parent != parent
            or repeated_candidate != candidate
            or repeated_training != training
            or component_identity(raw) != raw_before
            or component_identity(parent) != parent_before
            or component_identity(selected_adapter) != selected_before
        ):
            raise ArtifactError("fixed-v5 source changed during finalization")

        zero_identity = _published_identity(
            component_identity(staged_zero), output / "base_zero_adapter"
        )
        tokenizer_sha = semantic["shared_tokenizer_json_sha256"]
        template_sha = semantic["chat_template_sha256"]
        zero_config = staged_zero / "adapter_config.json"
        trained_config = selected_adapter / "adapter_config.json"
        base_composite = exact_lora_composite_sha256(
            parent_tree_sha256=raw_before["tree_sha256"],
            adapter_tree_sha256=zero_identity["tree_sha256"],
            adapter_config_sha256=sha256_file(zero_config),
            tokenizer_json_sha256=tokenizer_sha,
            chat_template_sha256=template_sha,
            dtype=campaign.model["dtype"],
        )
        trained_composite = exact_lora_composite_sha256(
            parent_tree_sha256=parent_before["tree_sha256"],
            adapter_tree_sha256=selected_before["tree_sha256"],
            adapter_config_sha256=sha256_file(trained_config),
            tokenizer_json_sha256=tokenizer_sha,
            chat_template_sha256=template_sha,
            dtype=campaign.model["dtype"],
        )
        if base_composite == trained_composite:
            raise ArtifactError("fixed-v5 exact-LoRA arms alias")
        pair_sha = _pair_sha256(base_composite, trained_composite)
        manifest = {
            "schema": EXACT_LORA_MANIFEST_SCHEMA,
            "status": "ok",
            "stage": "browser_action_correction",
            "correction_method": "fixed_v5_one_candidate_gate",
            "campaign_digest": campaign.digest,
            "artifact_source_git_sha": artifact_sha,
            "objective_scaffold": campaign.campaign["objective"]["scaffold"],
            "model_id": campaign.model["model_id"],
            "base_revision": campaign.model["revision"],
            "serving_mode": "exact_peft_lora",
            "final_model_directory_published": False,
            "bf16_weights_merged": False,
            "selected_checkpoint": {
                "name": "step26",
                "update": 26,
                "selection_performed": False,
                "adapter": str(selected_adapter),
                "adapter_tree_sha256": selected_before["tree_sha256"],
                "gate_manifest_sha256": sha256_file(gate_path),
            },
            "receipts": {
                "browser_action_fixed_v5_training": {
                    "path": str(receipt_path),
                    "schema": training["schema"],
                    "status": training["status"],
                    "sha256": sha256_file(receipt_path),
                    "receipt_body_sha256": training["receipt_body_sha256"],
                },
                "browser_action_fixed_v5_gate": {
                    "path": str(gate_path),
                    "schema": gate["schema"],
                    "status": gate["status"],
                    "sha256": sha256_file(gate_path),
                    "manifest_body_sha256": gate["manifest_body_sha256"],
                    "passed": True,
                    "gate_policy_sha256": gate["gate_policy_sha256"],
                },
                "selected_parent_merge": {
                    "path": str(parent / "merge_provenance.json"),
                    "sha256": sha256_file(parent / "merge_provenance.json"),
                },
                "raw_base_smoke": {
                    "path": str(smoke_path),
                    "sha256": sha256_file(smoke_path),
                },
            },
            "shared_tokenizer": {
                "path": str(parent),
                "tokenizer_json_sha256": tokenizer_sha,
                "raw_tokenizer_json_sha256": semantic["raw_tokenizer_json_sha256"],
                "semantic_sha256": semantic["semantic_sha256"],
                "serialized_bytes_equal": semantic["serialized_bytes_equal"],
                "chat_template_sha256": template_sha,
                "processor_class": tokenizer["processor_class"],
            },
            "arms": {
                "base": {
                    "parent": raw_before,
                    "adapter": zero_identity,
                    "adapter_config": zero_attestation["zero_config"],
                    "adapter_config_sha256": sha256_file(zero_config),
                    "served_model_name": "qwen35-27b-base-exact-lora",
                    "composite_sha256": base_composite,
                },
                "trained": {
                    "parent": parent_before,
                    "adapter": selected_before,
                    "adapter_config": zero_attestation["source_config"],
                    "adapter_config_sha256": sha256_file(trained_config),
                    "served_model_name": (
                        f"caveat-27b-fixed-v5-{artifact_sha[:12]}-exact-lora"
                    ),
                    "composite_sha256": trained_composite,
                },
            },
            "composite_pair_sha256": pair_sha,
            "zero_adapter_attestation": zero_attestation,
            "structural_attestation": structural,
            "inference": {
                "api": "openai_chat_completions",
                "dtype": campaign.model["dtype"],
                "native_context_tokens": campaign.model["native_context_tokens"],
                "language_model_only": campaign.model["language_model_only"],
                "reasoning_parser": campaign.model["reasoning_parser"],
                "tool_call_parser": campaign.model["tool_call_parser"],
                "enable_auto_tool_choice": True,
                "enable_prefix_caching": campaign.model["enable_prefix_caching"],
                "preserve_thinking": campaign.model["preserve_thinking"],
                "lora_rank": 64,
                "lora_alpha": 128,
            },
        }
        manifest_path = staging / "exact_lora_manifest.json"
        publish_json(manifest_path, manifest)
        final_manifest_path = output / manifest_path.name
        final_receipt = {
            "schema": FIXED_V5_FINALIZATION_RECEIPT_SCHEMA,
            "status": "ok",
            "campaign_digest": campaign.digest,
            "artifact_source_git_sha": artifact_sha,
            "manifest_path": str(final_manifest_path),
            "manifest_sha256": sha256_file(manifest_path),
            "composite_pair_sha256": pair_sha,
            "base_composite_sha256": base_composite,
            "trained_composite_sha256": trained_composite,
            "training_receipt_sha256": sha256_file(receipt_path),
            "gate_manifest_sha256": sha256_file(gate_path),
            "gate_policy_sha256": gate["gate_policy_sha256"],
            "selection_performed": False,
            "selected_update": 26,
        }
        final_receipt["receipt_body_sha256"] = sha256_bytes(
            canonical_json(final_receipt).encode()
        )
        publish_json(staging / "exact_lora_manifest_receipt.json", final_receipt)
        if output.exists() or output.is_symlink():
            raise ArtifactError("fixed-v5 final output appeared during publication")
        staging.replace(output)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise

    try:
        validate_fixed_v5_serving_manifest(
            output / "exact_lora_manifest.json", arm="base"
        )
        validate_fixed_v5_serving_manifest(
            output / "exact_lora_manifest.json", arm="trained"
        )
    except BaseException:
        if output.is_dir() and not output.is_symlink():
            shutil.rmtree(output)
        raise
    return manifest
