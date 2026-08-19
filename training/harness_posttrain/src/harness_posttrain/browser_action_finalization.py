"""Freeze the selected browser-action correction as an exact-LoRA pair.

This stage is intentionally structural and offline.  It revalidates the
continuation receipt, selection evidence, selected adapter, and every bound
hash before publishing.  It proves the baseline adapter is an exact additive
LoRA no-op and that the selected adapter has a nonzero LoRA delta without
loading or merging the 27B parent weights.  A separate GPU behavioral check
may be run later, but it is not substituted for this create-only gate.
"""

from __future__ import annotations

import importlib
import math
import re
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
    read_jsonl,
    sha256_bytes,
    sha256_file,
)
from .browser_action_continuation_receipt import CONTINUATION_RECEIPT_SCHEMA
from .browser_action_curriculum import _output_stack
from .browser_action_selection import (
    _COMPLETION_BUDGET,
    _HARD_GATES,
    _RANK_ORDER,
    _SELECTOR_TRANSPORT_POLICY,
    _SINGLE_ATTEMPT_EXECUTION,
    _UPDATES,
    ATTEMPT_RECEIPT_SCHEMA,
    CACHE_INDEX_SCHEMA,
    EVIDENCE_SCHEMA,
    SELECTION_SCHEMA,
    _attempt_inventory,
    _cache_inventory,
    _cache_name,
    _candidate_inventory,
    _choose,
    _evidence_key,
    _expected_specs,
    _metrics_from_evidence,
    _request_execution_summary,
    _selection_tasks,
)
from .browser_action_selection_v4 import (
    _ADAPTIVE_COUNTS,
    ADAPTIVE_SELECTION_SCHEMA,
    _validate_adaptive_selection_artifacts,
)
from .config import Campaign
from .exact_lora import (
    EXACT_LORA_MANIFEST_SCHEMA,
    ZERO_ADAPTER_SCHEMA,
    _verified_raw_base,
    component_identity,
    create_zero_adapter,
    exact_lora_composite_sha256,
    tokenizer_semantic_equivalence,
)
from .parent_merge import _validate_adapter_parent, _validate_parent

CORRECTION_FINALIZATION_RECEIPT_SCHEMA = (
    "harness-posttrain.browser-action-correction-exact-lora-receipt.v1"
)
LORA_STRUCTURAL_ATTESTATION_SCHEMA = (
    "harness-posttrain.browser-action-lora-structural-attestation.v1"
)

_ARTIFACT_SHA = re.compile(r"[0-9a-f]{40}")
_LORA_WEIGHT_KEY = re.compile(r"^(.*)\.lora_([AB])(?:\.[^.]+)?\.weight$")
_EXPECTED_UPDATES = tuple(_UPDATES)
_EXPECTED_NAMES = tuple(f"step{update}" for update in _EXPECTED_UPDATES)
_CONTINUATION_UPDATES = list(_EXPECTED_UPDATES[1:])
_REQUEST_COUNTS = {
    "contract": 64,
    "continue-incomplete": 8,
    "checkpoint-complete": 8,
    "repair-rejected": 8,
    "act-after-approval": 8,
    "total": 96,
}


def _verify_selection_execution_artifacts(
    *,
    selection_dir: Path,
    evidence: list[dict[str, Any]],
    manifest: Mapping[str, Any],
) -> None:
    """Recompute the cache and process-attempt provenance used by selection."""

    cache_dir = (selection_dir / "response_cache").resolve()
    cache_descriptor = manifest.get("response_cache")
    if not isinstance(cache_descriptor, Mapping):
        raise ArtifactError("selection manifest has no response-cache binding")
    expected_cache = _cache_inventory(cache_dir, evidence)
    if (
        cache_descriptor != expected_cache
        or cache_descriptor.get("schema") != CACHE_INDEX_SCHEMA
        or cache_descriptor.get("rows") != len(evidence)
    ):
        raise ArtifactError("selection response-cache inventory drifted")
    _same_path(
        cache_descriptor.get("directory"),
        cache_dir,
        label="selection response cache",
    )

    evidence_by_name = {_cache_name(row): row for row in evidence}
    if len(evidence_by_name) != len(evidence):
        raise ArtifactError("selection response-cache names are not unique")
    for descriptor in cache_descriptor.get("files", []):
        if not isinstance(descriptor, Mapping):
            raise ArtifactError("selection response-cache descriptor is malformed")
        name = descriptor.get("name")
        if not isinstance(name, str) or name not in evidence_by_name:
            raise ArtifactError("selection response-cache descriptor has an unknown name")
        cached = read_json(cache_dir / name)
        expected_row = evidence_by_name[name]
        if (
            cached != expected_row
            or descriptor.get("evidence_key") != list(_evidence_key(expected_row))
            or descriptor.get("request_sha256") != expected_row.get("request_sha256")
            or descriptor.get("selector_process_attempt_id")
            != expected_row.get("selector_process_attempt_id")
        ):
            raise ArtifactError("selection response cache differs from raw evidence")

    attempts = _attempt_inventory(selection_dir, evidence)
    if manifest.get("server_attempts") != attempts or not attempts:
        raise ArtifactError("selection server-attempt inventory drifted")
    for descriptor in attempts:
        if not isinstance(descriptor, Mapping):
            raise ArtifactError("selection server-attempt descriptor is malformed")
        receipt = read_json(str(descriptor.get("receipt", "")))
        status = descriptor.get("status")
        interrupted = status == "externally_interrupted"
        completed = descriptor.get("completed_in_attempt")
        if (
            receipt.get("schema") != ATTEMPT_RECEIPT_SCHEMA
            or receipt.get("status") != status
            or receipt.get("transport_policy") != _SELECTOR_TRANSPORT_POLICY
            or receipt.get("automatic_request_retries") != 0
            or receipt.get("same_process_request_resubmissions") != 0
            or receipt.get("external_process_interruption") is not interrupted
            or receipt.get("uncached_inflight_requests_may_be_reexecuted") is not interrupted
            or receipt.get("aggregate_zero_generation_resets_claimed") is not (
                not interrupted
            )
            or "failure" in receipt
            or type(completed) is not int
            or completed < 0
            or (
                completed > 0
                and any(
                    not isinstance(descriptor.get(name), str)
                    or not isinstance(descriptor.get(f"{name}_sha256"), str)
                    for name in ("server_command", "server_log")
                )
            )
        ):
            raise ArtifactError("selection process-attempt policy drifted")

    expected_summary = _request_execution_summary(evidence, attempts)
    if manifest.get("request_execution_summary") != expected_summary:
        raise ArtifactError("selection request execution summary drifted")
    if (
        expected_summary.get("accepted_response_count") != len(evidence)
        or expected_summary.get("accepted_response_http_attempt_count") != len(evidence)
        or expected_summary.get("accepted_response_retry_count") != 0
        or expected_summary.get("all_accepted_responses_completed_single_http_attempt")
        is not True
        or expected_summary.get("same_process_request_resubmissions") != 0
        or expected_summary.get("aggregate_zero_generation_resets_claimed")
        is not (
            expected_summary.get("externally_interrupted_process_attempt_count") == 0
        )
    ):
        raise ArtifactError("selection aggregate request-attempt policy drifted")


def _same_path(value: Any, expected: Path, *, label: str) -> None:
    if not isinstance(value, str) or Path(value).resolve() != expected:
        raise ArtifactError(f"{label} path is not the bounded campaign artifact")


def _bound_file(*, path_value: Any, digest_value: Any, expected: Path, label: str) -> str:
    _same_path(path_value, expected, label=label)
    digest = sha256_file(expected)
    if digest_value != digest:
        raise ArtifactError(f"{label} hash changed")
    return digest


def _correction_layout(root: Path, receipt_path: Path, selection_path: Path) -> tuple[Path, str]:
    try:
        relative = receipt_path.relative_to(root)
    except ValueError as exc:
        raise ArtifactError("continuation receipt is outside the campaign root") from exc
    parts = relative.parts
    if (
        len(parts) != 4
        or parts[0] != "browser_action_correction"
        or _ARTIFACT_SHA.fullmatch(parts[1]) is None
        or parts[2:] != ("training", "training_receipt.json")
    ):
        raise ArtifactError(
            "continuation receipt must be browser_action_correction/<sha>/training/"
            "training_receipt.json"
        )
    stage = root / parts[0] / parts[1]
    # The v2 transport silently restarted one long deterministic completion at
    # 600 seconds.  V3 removed that hidden retry but its uniform output ceiling
    # objectively censored ten requests.  Neither failed scientific attempt may
    # be finalized.  V4 is the reviewed adaptive-censoring correction; the
    # ordinary create-only path remains valid for older completed campaigns.
    allowed_selection_dirs = {"selection", "selection_nonbinding_v4"}
    if (
        selection_path.name != "selected_checkpoint.json"
        or selection_path.parent.parent.resolve() != stage.resolve()
        or selection_path.parent.name not in allowed_selection_dirs
    ):
        raise ArtifactError("selection manifest is outside the correction selection stage")
    return stage.resolve(), parts[1]


def _verify_receipt_hashes(
    campaign: Campaign,
    *,
    root: Path,
    stage: Path,
    receipt_path: Path,
    artifact_sha: str,
) -> dict[str, Any]:
    receipt = read_json(receipt_path)
    parent = (root / "selected/merged").resolve()
    if (
        not isinstance(receipt, dict)
        or receipt.get("schema") != CONTINUATION_RECEIPT_SCHEMA
        or receipt.get("status") != "ok"
        or receipt.get("stage") != "browser_action_continuation"
        or receipt.get("campaign_digest") != campaign.digest
        or receipt.get("artifact_source_git_sha") != artifact_sha
        or receipt.get("optimizer_updates") != 28
        or receipt.get("new_optimizer_updates") != 8
        or receipt.get("checkpoint_updates") != _CONTINUATION_UPDATES
        or receipt.get("source", {}).get("verified_unchanged_before_and_after") is not True
    ):
        raise ArtifactError("browser-action continuation receipt is incompatible")
    _same_path(receipt.get("parent_model"), parent, label="continuation parent")

    training = stage / "training"
    direct = (
        ("plan_sha256", training / "plan.json", "continuation plan"),
        (
            "config_sha256",
            training / "browser_action_continuation.toml",
            "continuation config",
        ),
        (
            "curriculum_manifest_sha256",
            stage / "curriculum/manifest.json",
            "continuation curriculum manifest",
        ),
        (
            "curriculum_data_sha256",
            stage / "curriculum/train.jsonl",
            "continuation curriculum data",
        ),
        ("prime_manifest_sha256", stage / "prime/manifest.json", "continuation PRIME manifest"),
        ("prime_parquet_sha256", stage / "prime/train.parquet", "continuation PRIME data"),
    )
    for key, path, label in direct:
        if receipt.get(key) != sha256_file(path):
            raise ArtifactError(f"{label} hash changed")

    provenance = parent / "merge_provenance.json"
    if receipt.get("parent_merge_provenance_sha256") != sha256_file(provenance):
        raise ArtifactError("selected parent provenance hash changed")

    source = receipt.get("source")
    if not isinstance(source, Mapping):
        raise ArtifactError("continuation receipt has no source bindings")
    _same_path(source.get("campaign_root"), root, label="source campaign root")
    source_files = (
        ("refinement_receipt_sha256", root / "refinement/training_receipt.json"),
        ("post_sft_receipt_sha256", root / "post_sft_receipt.json"),
        ("refinement_plan_sha256", root / "refinement/config/plan.json"),
        ("refinement_config_sha256", root / "refinement/config/refinement.toml"),
        (
            "refinement_dataset_manifest_sha256",
            root / "refinement/prime/manifest.json",
        ),
    )
    for key, path in source_files:
        if source.get(key) != sha256_file(path):
            raise ArtifactError(f"continuation source binding changed: {key}")

    checkpoints = receipt.get("checkpoints")
    if (
        not isinstance(checkpoints, list)
        or len(checkpoints) != len(_CONTINUATION_UPDATES)
        or [row.get("update") for row in checkpoints if isinstance(row, Mapping)]
        != _CONTINUATION_UPDATES
    ):
        raise ArtifactError("continuation receipt checkpoint schedule drifted")
    return receipt


def _verify_selection_inputs(root: Path, manifest: Mapping[str, Any]) -> None:
    inputs = manifest.get("inputs")
    if not isinstance(inputs, Mapping):
        raise ArtifactError("selection manifest has no sealed input bindings")
    expected = (
        (
            "raw_manifest",
            "raw_manifest_sha256",
            root / "corpus/raw/manifest.json",
            "selection raw manifest",
        ),
        (
            "split_manifest",
            "split_manifest_sha256",
            root / "corpus/splits/manifest.json",
            "selection split manifest",
        ),
        (
            "selection_contract_tasks",
            "selection_contract_tasks_sha256",
            root / "corpus/raw/selection_contract_tasks.jsonl",
            "selection contract tasks",
        ),
        (
            "selection_checkpoint_tasks",
            "selection_checkpoint_tasks_sha256",
            root / "corpus/raw/selection_checkpoint_tasks.jsonl",
            "selection checkpoint tasks",
        ),
    )
    for path_key, hash_key, path, label in expected:
        _bound_file(
            path_value=inputs.get(path_key),
            digest_value=inputs.get(hash_key),
            expected=path.resolve(),
            label=label,
        )
    split = read_json(root / "corpus/splits/manifest.json")
    if (
        not isinstance(split, dict)
        or inputs.get("split_manifest_body_sha256") != split.get("manifest_body_sha256")
        or inputs.get("contract_tasks") != 64
        or inputs.get("checkpoint_tasks") != 8
        or inputs.get("source") != "procedural"
        or inputs.get("split") != "selection"
        or inputs.get("amazon_tasks") != 0
    ):
        raise ArtifactError("selection sealed-input declaration drifted")


def _verify_selection(
    campaign: Campaign,
    *,
    root: Path,
    stage: Path,
    selection_path: Path,
    parent: Path,
    inventory: Mapping[str, Mapping[str, Any]],
    bindings: Mapping[str, Any],
) -> dict[str, Any]:
    manifest = read_json(selection_path)
    if not isinstance(manifest, dict):
        raise ArtifactError("browser-action selection manifest is invalid")
    body = dict(manifest)
    body_sha256 = body.pop("manifest_body_sha256", None)
    if body_sha256 != sha256_bytes(canonical_json(body).encode()):
        raise ArtifactError("browser-action selection manifest body hash changed")
    adaptive = selection_path.parent.name == "selection_nonbinding_v4"
    expected_schema = ADAPTIVE_SELECTION_SCHEMA if adaptive else SELECTION_SCHEMA
    if (
        manifest.get("schema") != expected_schema
        or manifest.get("status") != "complete"
        or manifest.get("campaign_digest") != campaign.digest
        or manifest.get("selection_split") != "procedural_validation"
        or manifest.get("amazon_data_used") is not False
        or manifest.get("candidate_count") != len(_EXPECTED_NAMES)
        or tuple(sorted(manifest.get("candidate_inventory", {}))) != tuple(sorted(_EXPECTED_NAMES))
        or manifest.get("candidate_inventory") != dict(inventory)
        or manifest.get("training_bindings") != dict(bindings)
        or manifest.get("hard_gates") != _HARD_GATES
        or manifest.get("rank_order") != list(_RANK_ORDER)
        or manifest.get("fallback") != "step20"
        or manifest.get("request_counts_per_candidate") != _REQUEST_COUNTS
    ):
        raise ArtifactError("browser-action selection manifest is incompatible")
    if adaptive:
        if (
            "completion_budget" in manifest
            or "completion_ceiling_bound" in manifest
            or manifest.get("accepted_response_completion_ceiling_bound") is not False
            or manifest.get("first_tier_completion_ceiling_bound_observed") is not True
        ):
            raise ArtifactError("adaptive selection cannot claim one homogeneous budget")
    elif (
        manifest.get("completion_ceiling_bound") is not False
        or manifest.get("completion_budget") != _COMPLETION_BUDGET
    ):
        raise ArtifactError("browser-action selection completion budget drifted")
    _same_path(manifest.get("base_model"), parent, label="selection parent")
    _verify_selection_inputs(root, manifest)

    evidence_descriptor = manifest.get("raw_evidence")
    selection_dir = selection_path.parent.resolve()
    evidence_path = (selection_dir / "raw_evidence.jsonl").resolve()
    if not isinstance(evidence_descriptor, Mapping):
        raise ArtifactError("selection manifest has no raw evidence binding")
    _bound_file(
        path_value=evidence_descriptor.get("path"),
        digest_value=evidence_descriptor.get("sha256"),
        expected=evidence_path,
        label="selection raw evidence",
    )
    evidence = read_jsonl(evidence_path)
    if evidence_descriptor.get("rows") != len(evidence) or len(evidence) != 480:
        raise ArtifactError("selection raw evidence row count drifted")
    if adaptive:
        contracts, checkpoints, exact_inputs = _selection_tasks(campaign, root)
        if manifest.get("inputs") != exact_inputs:
            raise ArtifactError("adaptive selection sealed inputs drifted")
        system, output_model = _output_stack()
        specs = _expected_specs(
            candidate_names=set(inventory),
            contracts=contracts,
            checkpoints=checkpoints,
            system=system,
            output_model=output_model,
        )
        summary = _validate_adaptive_selection_artifacts(
            selection_dir, manifest, specs, output_model
        )
        if (
            summary.get("imported_natural_stop_responses")
            != _ADAPTIVE_COUNTS["imported_natural_stop_responses"]
            or summary.get("escalated_natural_stop_responses")
            != _ADAPTIVE_COUNTS["escalated_natural_stop_responses"]
            or summary.get("accepted_responses") != _ADAPTIVE_COUNTS["accepted_responses"]
            or summary.get("generation_attempts_lower_bound")
            != _ADAPTIVE_COUNTS["total_generation_attempts"]
            or summary.get("total_generation_attempts")
            not in {_ADAPTIVE_COUNTS["total_generation_attempts"], None}
            or (
                summary.get("externally_interrupted_adaptive_process_attempts") == 0
                and summary.get("total_generation_attempts")
                != _ADAPTIVE_COUNTS["total_generation_attempts"]
            )
            or summary.get("aggregate_zero_generation_resets_claimed") is not False
            or summary.get("outcome_blind_escalation") is not True
        ):
            raise ArtifactError("adaptive selection execution policy drifted")
    else:
        if any(
            row.get("schema") != EVIDENCE_SCHEMA
            or row.get("completion_ceiling_bound") is not False
            or row.get("completion_budget") != _COMPLETION_BUDGET
            or row.get("request_execution") != _SINGLE_ATTEMPT_EXECUTION
            or type(row.get("selector_process_attempt_id")) is not int
            or row["selector_process_attempt_id"] < 1
            or row.get("request_sha256")
            != sha256_bytes(canonical_json(row.get("request")).encode())
            or row.get("response_sha256")
            != sha256_bytes(canonical_json(row.get("response")).encode())
            for row in evidence
        ):
            raise ArtifactError("selection raw evidence payload hash changed")
        _verify_selection_execution_artifacts(
            selection_dir=selection_dir,
            evidence=evidence,
            manifest=manifest,
        )
    metrics = _metrics_from_evidence(evidence, set(_EXPECTED_NAMES))
    if manifest.get("candidates") != metrics:
        raise ArtifactError("selection metrics differ from the hash-bound raw evidence")

    selected_name, reason = _choose(metrics)
    selected = manifest.get("selected")
    if not isinstance(selected, Mapping):
        raise ArtifactError("selection manifest has no selected checkpoint")
    selected_update = selected.get("update")
    if (
        selected_name not in _EXPECTED_NAMES
        or selected_update not in _EXPECTED_UPDATES
        or selected.get("name") != selected_name
        or selected_name != f"step{selected_update}"
        or selected.get("adapter") != inventory[selected_name].get("path")
        or selected.get("adapter_tree_sha256") != inventory[selected_name].get("tree_sha256")
        or selected.get("reason") != reason
        or selected.get("metrics") != metrics[selected_name]
    ):
        raise ArtifactError("selected checkpoint is not the verified bounded selection")
    passing = sorted(name for name, value in metrics.items() if value["passes_hard_gates"])
    if manifest.get("passing_candidates") != passing:
        raise ArtifactError("selection passing-candidate inventory drifted")
    return manifest


def _verify_inputs(
    campaign: Campaign,
    *,
    root: Path,
    stage: Path,
    artifact_sha: str,
    receipt_path: Path,
    selection_path: Path,
) -> dict[str, Any]:
    receipt = _verify_receipt_hashes(
        campaign,
        root=root,
        stage=stage,
        receipt_path=receipt_path,
        artifact_sha=artifact_sha,
    )
    parent, _paths, inventory, bindings = _candidate_inventory(campaign, root, stage / "training")
    expected_receipt = receipt_path.resolve()
    _same_path(
        bindings.get("continuation_training_receipt"),
        expected_receipt,
        label="selection continuation receipt",
    )
    if bindings.get("continuation_training_receipt_sha256") != sha256_file(expected_receipt):
        raise ArtifactError("selection continuation receipt hash changed")
    selection = _verify_selection(
        campaign,
        root=root,
        stage=stage,
        selection_path=selection_path,
        parent=parent,
        inventory=inventory,
        bindings=bindings,
    )
    selected_name = str(selection["selected"]["name"])
    selected_adapter = Path(str(inventory[selected_name]["path"])).resolve()
    _validate_parent(parent)
    _validate_adapter_parent(selected_adapter, parent)
    return {
        "receipt": receipt,
        "receipt_sha256": sha256_file(receipt_path),
        "selection": selection,
        "selection_sha256": sha256_file(selection_path),
        "parent": parent,
        "inventory": dict(inventory),
        "bindings": dict(bindings),
        "selected_name": selected_name,
        "selected_update": int(selection["selected"]["update"]),
        "selected_adapter": selected_adapter,
    }


def _standard_lora_config(adapter: Path) -> dict[str, Any]:
    config = read_json(adapter / "adapter_config.json")
    if not isinstance(config, dict):
        raise ArtifactError("selected adapter configuration is invalid")
    if (
        config.get("bias", "none") != "none"
        or config.get("modules_to_save") not in (None, [])
        or config.get("use_dora", False) is not False
        or config.get("megatron_config") is not None
    ):
        raise ArtifactError("offline no-op proof requires an additive standard LoRA adapter")
    return config


def _lora_delta_attestation(adapter: Path) -> dict[str, Any]:
    """Prove that at least one selected LoRA B@A operator is nonzero."""

    try:
        import torch
        from safetensors import safe_open
    except ImportError as exc:  # pragma: no cover - cluster finalization dependency
        raise ArtifactError(
            "torch and safetensors are required for the LoRA structural gate"
        ) from exc

    weights = sorted(adapter.glob("adapter_model*.safetensors"))
    if len(weights) != 1 or list(adapter.glob("adapter_model*.bin")):
        raise ArtifactError("structural gate requires exactly one safetensors adapter")
    pairs: dict[str, dict[str, str]] = {}
    with safe_open(weights[0], framework="pt", device="cpu") as stream:
        for key in sorted(stream.keys()):
            match = _LORA_WEIGHT_KEY.fullmatch(key)
            if match is None:
                raise ArtifactError(f"selected adapter has a non-LoRA tensor: {key}")
            pairs.setdefault(match.group(1), {})[match.group(2)] = key
        if not pairs or any(set(pair) != {"A", "B"} for pair in pairs.values()):
            raise ArtifactError("selected adapter has incomplete LoRA A/B tensor pairs")

        records: list[dict[str, Any]] = []
        nonzero_pairs = 0
        for module, keys in sorted(pairs.items()):
            a = stream.get_tensor(keys["A"])
            b = stream.get_tensor(keys["B"])
            if a.ndim != 2 or b.ndim != 2 or a.shape[0] != b.shape[1]:
                raise ArtifactError(f"selected adapter has incompatible LoRA shapes: {module}")
            a64 = a.to(dtype=torch.float64)
            b64 = b.to(dtype=torch.float64)
            squared_norm = float(
                torch.trace((b64.transpose(0, 1) @ b64) @ (a64 @ a64.transpose(0, 1)))
            )
            if not math.isfinite(squared_norm) or squared_norm < 0.0:
                raise ArtifactError(f"selected adapter delta norm is invalid: {module}")
            is_nonzero = squared_norm > 0.0
            nonzero_pairs += int(is_nonzero)
            records.append(
                {
                    "module": module,
                    "rank": int(a.shape[0]),
                    "input_features": int(a.shape[1]),
                    "output_features": int(b.shape[0]),
                    "delta_frobenius_squared": squared_norm,
                    "nonzero": is_nonzero,
                }
            )
    if nonzero_pairs == 0:
        raise ArtifactError("selected trained adapter has no nonzero LoRA delta")
    return {
        "pair_count": len(records),
        "nonzero_delta_pairs": nonzero_pairs,
        "pairs": records,
    }


def _tokenizer_attestation(raw: Path, parent: Path) -> dict[str, Any]:
    try:
        model_smoke = importlib.import_module("harness_distill.model_smoke")
        raw_processor, raw_tokenizer = model_smoke._load_processor_tokenizer(raw)  # noqa: SLF001
        parent_processor, parent_tokenizer = model_smoke._load_processor_tokenizer(  # noqa: SLF001
            parent
        )
    except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ArtifactError("raw and selected-parent tokenizers cannot be loaded offline") from exc
    raw_processor_class = f"{type(raw_processor).__module__}.{type(raw_processor).__qualname__}"
    processor_class = f"{type(parent_processor).__module__}.{type(parent_processor).__qualname__}"
    if raw_processor_class != processor_class:
        raise ArtifactError("selected parent changed the raw model processor class")
    semantic = tokenizer_semantic_equivalence(
        raw_tokenizer,
        parent_tokenizer,
        raw_tokenizer_json=raw / "tokenizer.json",
        shared_tokenizer_json=parent / "tokenizer.json",
    )
    return {"processor_class": processor_class, "semantic": semantic}


def _structural_attestation(
    selected_adapter: Path, zero_attestation: Mapping[str, Any]
) -> dict[str, Any]:
    config = _standard_lora_config(selected_adapter)
    source_inventory = zero_attestation.get("source_tensor_inventory")
    zero_inventory = zero_attestation.get("zero_tensor_inventory")
    if (
        zero_attestation.get("schema") != ZERO_ADAPTER_SCHEMA
        or zero_attestation.get("status") != "ok"
        or not isinstance(source_inventory, Mapping)
        or not isinstance(zero_inventory, Mapping)
        or source_inventory.get("all_zero") is True
        or int(source_inventory.get("nonzero_elements", 0)) <= 0
        or zero_inventory.get("all_zero") is not True
        or zero_inventory.get("nonzero_elements") != 0
    ):
        raise ArtifactError("zero-adapter construction did not prove zero and trained structure")
    delta = _lora_delta_attestation(selected_adapter)
    return {
        "schema": LORA_STRUCTURAL_ATTESTATION_SCHEMA,
        "status": "ok",
        "proof_mode": "offline_structural",
        "adapter_type": "standard_additive_lora",
        "zero_noop": {
            "passed": True,
            "exact": True,
            "basis": "all serialized tensors are zero; no bias, DoRA, or modules-to-save",
            "nonzero_elements": 0,
        },
        "trained_nonzero": {
            "passed": True,
            "serialized_nonzero_elements": source_inventory["nonzero_elements"],
            **delta,
        },
        "config": {
            "rank": config.get("r"),
            "alpha": config.get("lora_alpha"),
            "bias": config.get("bias", "none"),
            "use_dora": config.get("use_dora", False),
            "modules_to_save": config.get("modules_to_save"),
        },
        "gpu_behavioral_gate": "separate_not_run",
    }


def _published_identity(identity: Mapping[str, Any], path: Path) -> dict[str, Any]:
    return {**dict(identity), "path": str(path.resolve())}


def validate_browser_action_serving_manifest(
    manifest_path: str | Path, *, arm: str
) -> dict[str, Any]:
    """Re-hash both corrected serving arms and return one arm's launch parameters."""

    path = Path(manifest_path).resolve()
    # The committed evaluator calls this authoritative API for every corrected
    # endpoint.  Dispatch the separately attested fixed-v5 layout without
    # weakening any legacy correction checks below.
    if path.parent.parent.parent.name == "browser_action_fixed_v5":
        from .browser_action_fixed_v5_finalization import (
            validate_fixed_v5_serving_manifest,
        )

        return validate_fixed_v5_serving_manifest(path, arm=arm)
    if arm not in {"base", "trained"}:
        raise ArtifactError("exact-LoRA serving arm must be base or trained")
    if path.name != "exact_lora_manifest.json" or path.parent.name != "final":
        raise ArtifactError("corrected exact-LoRA manifest is outside its final directory")
    stage = path.parent.parent
    artifact_sha = stage.name
    if (
        stage.parent.name != "browser_action_correction"
        or _ARTIFACT_SHA.fullmatch(artifact_sha) is None
    ):
        raise ArtifactError("corrected exact-LoRA manifest is outside a source-SHA stage")
    manifest = read_json(path)
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema") != EXACT_LORA_MANIFEST_SCHEMA
        or manifest.get("status") != "ok"
        or manifest.get("stage") != "browser_action_correction"
        or manifest.get("artifact_source_git_sha") != artifact_sha
        or manifest.get("serving_mode") != "exact_peft_lora"
        or manifest.get("final_model_directory_published") is not False
        or manifest.get("bf16_weights_merged") is not False
        or (path.parent / "model").exists()
    ):
        raise ArtifactError("corrected exact-LoRA serving manifest is incompatible")
    receipt = read_json(path.parent / "exact_lora_manifest_receipt.json")
    if (
        not isinstance(receipt, dict)
        or receipt.get("schema") != CORRECTION_FINALIZATION_RECEIPT_SCHEMA
        or receipt.get("status") != "ok"
        or receipt.get("manifest_path") != str(path)
        or receipt.get("manifest_sha256") != sha256_file(path)
        or receipt.get("composite_pair_sha256") != manifest.get("composite_pair_sha256")
    ):
        raise ArtifactError("corrected exact-LoRA manifest receipt is incompatible")

    shared = manifest.get("shared_tokenizer")
    if not isinstance(shared, Mapping):
        raise ArtifactError("corrected exact-LoRA manifest has no shared tokenizer")
    tokenizer_path = Path(str(shared.get("path", ""))).resolve()
    if sha256_file(tokenizer_path / "tokenizer.json") != shared.get("tokenizer_json_sha256"):
        raise ArtifactError("corrected exact-LoRA shared tokenizer bytes changed")
    descriptors = manifest.get("arms")
    if not isinstance(descriptors, Mapping) or set(descriptors) != {"base", "trained"}:
        raise ArtifactError("corrected exact-LoRA manifest must contain exactly two arms")
    validated: dict[str, dict[str, str]] = {}
    for name in ("base", "trained"):
        descriptor = descriptors.get(name)
        if not isinstance(descriptor, Mapping):  # pragma: no cover - guarded above
            raise ArtifactError(f"corrected exact-LoRA manifest has no {name} arm")
        parent = Path(str(descriptor.get("parent", {}).get("path", ""))).resolve()
        adapter = Path(str(descriptor.get("adapter", {}).get("path", ""))).resolve()
        if (
            component_identity(parent) != descriptor.get("parent")
            or component_identity(adapter) != descriptor.get("adapter")
            or sha256_file(adapter / "adapter_config.json")
            != descriptor.get("adapter_config_sha256")
        ):
            raise ArtifactError(f"corrected exact-LoRA {name} component bytes changed")
        composite = exact_lora_composite_sha256(
            parent_tree_sha256=str(descriptor["parent"]["tree_sha256"]),
            adapter_tree_sha256=str(descriptor["adapter"]["tree_sha256"]),
            adapter_config_sha256=str(descriptor["adapter_config_sha256"]),
            tokenizer_json_sha256=str(shared["tokenizer_json_sha256"]),
            chat_template_sha256=str(shared["chat_template_sha256"]),
            dtype=str(manifest.get("inference", {}).get("dtype", "")),
        )
        expected_trained = f"qwen35-browser-action-corrected-{artifact_sha[:12]}-exact-lora"
        expected_name = "qwen35-27b-base-exact-lora" if name == "base" else expected_trained
        served_name = descriptor.get("served_model_name")
        if descriptor.get("composite_sha256") != composite or served_name != expected_name:
            raise ArtifactError(f"corrected exact-LoRA {name} serving identity changed")
        validated[name] = {
            "parent": str(parent),
            "adapter": str(adapter),
            "served_model_name": str(served_name),
            "composite_sha256": composite,
        }

    pair_sha256 = sha256_bytes(
        canonical_json(
            {
                "schema": EXACT_LORA_MANIFEST_SCHEMA,
                "base": validated["base"]["composite_sha256"],
                "trained": validated["trained"]["composite_sha256"],
            }
        ).encode()
    )
    if manifest.get("composite_pair_sha256") != pair_sha256:
        raise ArtifactError("corrected exact-LoRA composite pair identity changed")

    selected = manifest.get("selected_checkpoint")
    if (
        not isinstance(selected, Mapping)
        or selected.get("name") != f"step{selected.get('update')}"
        or selected.get("update") not in _EXPECTED_UPDATES
        or Path(str(selected.get("adapter", ""))).resolve() != Path(validated["trained"]["adapter"])
        or selected.get("adapter_tree_sha256") != descriptors["trained"]["adapter"]["tree_sha256"]
    ):
        raise ArtifactError("corrected exact-LoRA selected-checkpoint binding changed")
    receipts = manifest.get("receipts")
    if not isinstance(receipts, Mapping):
        raise ArtifactError("corrected exact-LoRA source receipts are absent")
    continuation = receipts.get("browser_action_continuation")
    selection = receipts.get("browser_action_selection")
    expected_receipts = (
        (
            continuation,
            stage / "training/training_receipt.json",
            CONTINUATION_RECEIPT_SCHEMA,
            "ok",
            "continuation",
        ),
        (
            selection,
            Path(str(selection.get("path", ""))).resolve()
            if isinstance(selection, Mapping)
            else stage / "selection/selected_checkpoint.json",
            ADAPTIVE_SELECTION_SCHEMA
            if isinstance(selection, Mapping)
            and selection.get("path")
            and Path(str(selection.get("path"))).parent.name == "selection_nonbinding_v4"
            else SELECTION_SCHEMA,
            "complete",
            "selection",
        ),
    )
    for descriptor, expected_path, schema, status, label in expected_receipts:
        if not isinstance(descriptor, Mapping):
            raise ArtifactError(f"corrected exact-LoRA {label} receipt is absent")
        _bound_file(
            path_value=descriptor.get("path"),
            digest_value=descriptor.get("sha256"),
            expected=expected_path.resolve(),
            label=f"corrected exact-LoRA {label}",
        )
        if descriptor.get("schema") != schema or descriptor.get("status") != status:
            raise ArtifactError(f"corrected exact-LoRA {label} receipt status changed")
        if label == "selection" and (
            expected_path.name != "selected_checkpoint.json"
            or expected_path.parent.parent.resolve() != stage.resolve()
            or expected_path.parent.name not in {"selection", "selection_nonbinding_v4"}
            or (
                expected_path.parent.name == "selection_nonbinding_v4"
                and descriptor.get("schema") != ADAPTIVE_SELECTION_SCHEMA
            )
            or (
                expected_path.parent.name == "selection"
                and descriptor.get("schema") != SELECTION_SCHEMA
            )
        ):
            raise ArtifactError("corrected exact-LoRA selection receipt path changed")
    if selection.get("schema") == ADAPTIVE_SELECTION_SCHEMA:
        selection_record = read_json(expected_receipts[1][1])
        selection_body = dict(selection_record) if isinstance(selection_record, Mapping) else {}
        selection_body_sha256 = selection_body.pop("manifest_body_sha256", None)
        expected_summary = selection_record.get("adaptive_execution")
        exact_counts = all(
            isinstance(expected_summary, Mapping)
            and expected_summary.get(name) == count
            for name, count in _ADAPTIVE_COUNTS.items()
            if name != "total_generation_attempts"
        ) and (
            expected_summary.get("generation_attempts_lower_bound")
            == _ADAPTIVE_COUNTS["total_generation_attempts"]
        )
        if (
            not isinstance(selection_record, Mapping)
            or selection_body_sha256
            != sha256_bytes(canonical_json(selection_body).encode())
            or selection.get("manifest_body_sha256") != selection_body_sha256
            or not exact_counts
            or expected_summary.get("total_generation_attempts")
            not in {_ADAPTIVE_COUNTS["total_generation_attempts"], None}
            or (
                expected_summary.get("externally_interrupted_adaptive_process_attempts") == 0
                and expected_summary.get("total_generation_attempts")
                != _ADAPTIVE_COUNTS["total_generation_attempts"]
            )
            or expected_summary.get("aggregate_zero_generation_resets_claimed") is not False
            or expected_summary.get("outcome_blind_escalation") is not True
            or selection.get("adaptive_execution") != expected_summary
            or receipt.get("adaptive_execution") != expected_summary
            or receipt.get("selection_manifest_sha256") != selection.get("sha256")
        ):
            raise ArtifactError("corrected exact-LoRA adaptive selection binding changed")
        for field, propagated, filename in (
            ("v3_import", "v3_import_sha256", "v3_import_provenance.json"),
            (
                "escalation_allowlist",
                "escalation_allowlist_sha256",
                "escalation_allowlist.json",
            ),
        ):
            source = selection_record.get(field)
            if (
                not isinstance(source, Mapping)
                or source.get("sha256") != selection.get(propagated)
                or source.get("sha256") != receipt.get(propagated)
            ):
                raise ArtifactError(
                    f"corrected exact-LoRA adaptive {field} binding changed"
                )
            _bound_file(
                path_value=source.get("path"),
                digest_value=source.get("sha256"),
                expected=expected_path.parent / filename,
                label=f"corrected exact-LoRA adaptive {field}",
            )
        attempts = selection_record.get("escalation_attempts")
        if (
            not isinstance(attempts, list)
            or not attempts
            or selection.get("escalation_attempts") != attempts
            or receipt.get("escalation_attempts") != attempts
        ):
            raise ArtifactError(
                "corrected exact-LoRA adaptive escalation attempts binding changed"
            )
        selection_dir = expected_path.parent
        for index, attempt in enumerate(attempts, 1):
            attempt_dir = selection_dir / "escalation_attempts" / f"attempt-{index:04d}"
            if not isinstance(attempt, Mapping) or attempt.get("attempt_id") != index:
                raise ArtifactError(
                    "corrected exact-LoRA adaptive escalation attempt identity changed"
                )
            for path_field, filename in (
                ("start", "start.json"),
                ("receipt", "receipt.json"),
                ("server_command", "server/command.json"),
                ("server_log", "server/vllm.log"),
            ):
                value = attempt.get(path_field)
                digest = attempt.get(f"{path_field}_sha256")
                if value is None and digest is None and path_field.startswith("server_"):
                    continue
                _bound_file(
                    path_value=value,
                    digest_value=digest,
                    expected=attempt_dir / filename,
                    label=f"corrected exact-LoRA adaptive attempt {index} {path_field}",
                )
        provenance = read_json(selection_dir / "v3_import_provenance.json")
        if not isinstance(provenance, Mapping):
            raise ArtifactError("corrected exact-LoRA adaptive v3 provenance is malformed")
        for path_field, hash_field in (
            ("source_start", "source_start_sha256"),
            ("source_failed_receipt", "source_failed_receipt_sha256"),
            ("source_server_command", "source_server_command_sha256"),
            ("source_server_log", "source_server_log_sha256"),
        ):
            source_path = Path(str(provenance.get(path_field, ""))).resolve()
            if sha256_file(source_path) != provenance.get(hash_field):
                raise ArtifactError(
                    f"corrected exact-LoRA adaptive {path_field} bytes changed"
                )
        source_cache = Path(str(provenance.get("source_directory", ""))).resolve() / (
            "response_cache"
        )
        files = provenance.get("files")
        if not isinstance(files, list) or len(files) != 470:
            raise ArtifactError("corrected exact-LoRA adaptive v3 cache binding changed")
        for cached in files:
            if not isinstance(cached, Mapping):
                raise ArtifactError("corrected exact-LoRA adaptive v3 cache is malformed")
            name = cached.get("name")
            if (
                not isinstance(name, str)
                or Path(name).name != name
                or sha256_file(source_cache / name) != cached.get("sha256")
            ):
                raise ArtifactError("corrected exact-LoRA adaptive v3 cache bytes changed")
    elif any(
        name in selection or name in receipt
        for name in (
            "adaptive_execution",
            "v3_import_sha256",
            "escalation_allowlist_sha256",
            "escalation_attempts",
        )
    ):
        raise ArtifactError("legacy selection claims adaptive provenance")
    structural = manifest.get("structural_attestation", {})
    if (
        structural.get("status") != "ok"
        or structural.get("zero_noop", {}).get("passed") is not True
        or structural.get("trained_nonzero", {}).get("passed") is not True
    ):
        raise ArtifactError("corrected exact-LoRA structural attestation is incomplete")
    requested = validated[arm]
    return {
        **requested,
        "tokenizer": str(tokenizer_path),
        "manifest_path": str(path),
        "manifest_sha256": sha256_file(path),
        "manifest_receipt_path": str(path.parent / "exact_lora_manifest_receipt.json"),
        "manifest_receipt_sha256": sha256_file(path.parent / "exact_lora_manifest_receipt.json"),
        "campaign_digest": str(manifest.get("campaign_digest")),
        "artifact_source_git_sha": artifact_sha,
        "selected_checkpoint": dict(selected),
        "continuation_receipt": dict(continuation),
        "selection_receipt": dict(selection),
    }


def finalize_browser_action_correction(
    campaign: Campaign,
    *,
    campaign_root: str | Path,
    continuation_receipt: str | Path,
    selection_manifest: str | Path,
    raw_base: str | Path,
) -> dict[str, Any]:
    """Publish a create-only exact-LoRA serving manifest for the selected correction.

    The output is fixed at
    ``browser_action_correction/<artifact_sha>/final`` next to the two required
    input artifacts.  No parent or adapter weights are copied or merged.
    """

    root = Path(campaign_root).resolve()
    receipt_path = Path(continuation_receipt).resolve()
    selection_path = Path(selection_manifest).resolve()
    raw = Path(raw_base).resolve()
    stage, artifact_sha = _correction_layout(root, receipt_path, selection_path)
    output = stage / "final"
    if output.exists() or output.is_symlink():
        raise ArtifactError(
            "browser-action final output already exists; publication is create-only"
        )
    if not stage.is_dir() or stage.is_symlink():
        raise ArtifactError("browser-action correction stage is absent or unsafe")

    inputs = _verify_inputs(
        campaign,
        root=root,
        stage=stage,
        artifact_sha=artifact_sha,
        receipt_path=receipt_path,
        selection_path=selection_path,
    )
    smoke_path = _verified_raw_base(campaign, root, raw)
    parent = inputs["parent"]
    selected_adapter = inputs["selected_adapter"]
    if raw == parent or selected_adapter in {raw, parent}:
        raise ArtifactError("exact-LoRA source components unexpectedly alias")

    raw_before = component_identity(raw)
    parent_before = component_identity(parent)
    selected_before = component_identity(selected_adapter)
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

        # Re-run the complete receipt/selection chain after inspecting and
        # deriving from the selected adapter.  Any concurrent or accidental
        # source mutation aborts before the final directory is visible.
        inputs_after = _verify_inputs(
            campaign,
            root=root,
            stage=stage,
            artifact_sha=artifact_sha,
            receipt_path=receipt_path,
            selection_path=selection_path,
        )
        if (
            inputs_after != inputs
            or component_identity(raw) != raw_before
            or component_identity(parent) != parent_before
            or component_identity(selected_adapter) != selected_before
        ):
            raise ArtifactError("a source artifact changed during correction finalization")

        zero_identity = _published_identity(
            component_identity(staged_zero), output / "base_zero_adapter"
        )
        trained_config_path = selected_adapter / "adapter_config.json"
        zero_config_path = staged_zero / "adapter_config.json"
        tokenizer_sha256 = semantic["shared_tokenizer_json_sha256"]
        template_sha256 = semantic["chat_template_sha256"]
        base_composite = exact_lora_composite_sha256(
            parent_tree_sha256=raw_before["tree_sha256"],
            adapter_tree_sha256=zero_identity["tree_sha256"],
            adapter_config_sha256=sha256_file(zero_config_path),
            tokenizer_json_sha256=tokenizer_sha256,
            chat_template_sha256=template_sha256,
            dtype=campaign.model["dtype"],
        )
        trained_composite = exact_lora_composite_sha256(
            parent_tree_sha256=parent_before["tree_sha256"],
            adapter_tree_sha256=selected_before["tree_sha256"],
            adapter_config_sha256=sha256_file(trained_config_path),
            tokenizer_json_sha256=tokenizer_sha256,
            chat_template_sha256=template_sha256,
            dtype=campaign.model["dtype"],
        )
        pair_sha256 = sha256_bytes(
            canonical_json(
                {
                    "schema": EXACT_LORA_MANIFEST_SCHEMA,
                    "base": base_composite,
                    "trained": trained_composite,
                }
            ).encode()
        )

        manifest = {
            "schema": EXACT_LORA_MANIFEST_SCHEMA,
            "status": "ok",
            "stage": "browser_action_correction",
            "campaign_digest": campaign.digest,
            "artifact_source_git_sha": artifact_sha,
            "objective_scaffold": campaign.campaign["objective"]["scaffold"],
            "model_id": campaign.model["model_id"],
            "base_revision": campaign.model["revision"],
            "serving_mode": "exact_peft_lora",
            "final_model_directory_published": False,
            "bf16_weights_merged": False,
            "selected_checkpoint": {
                "name": inputs["selected_name"],
                "update": inputs["selected_update"],
                "adapter": str(selected_adapter),
                "adapter_tree_sha256": selected_before["tree_sha256"],
            },
            "receipts": {
                "browser_action_continuation": {
                    "path": str(receipt_path),
                    "schema": inputs["receipt"]["schema"],
                    "status": inputs["receipt"]["status"],
                    "sha256": inputs["receipt_sha256"],
                },
                "browser_action_selection": {
                    "path": str(selection_path),
                    "schema": inputs["selection"]["schema"],
                    "status": inputs["selection"]["status"],
                    "sha256": inputs["selection_sha256"],
                    "manifest_body_sha256": inputs["selection"]["manifest_body_sha256"],
                    **(
                        {
                            "adaptive_execution": inputs["selection"]["adaptive_execution"],
                            "v3_import_sha256": inputs["selection"]["v3_import"]["sha256"],
                            "escalation_allowlist_sha256": inputs["selection"][
                                "escalation_allowlist"
                            ]["sha256"],
                            "escalation_attempts": inputs["selection"][
                                "escalation_attempts"
                            ],
                        }
                        if inputs["selection"]["schema"] == ADAPTIVE_SELECTION_SCHEMA
                        else {}
                    ),
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
                "tokenizer_json_sha256": tokenizer_sha256,
                "raw_tokenizer_json_sha256": semantic["raw_tokenizer_json_sha256"],
                "semantic_sha256": semantic["semantic_sha256"],
                "serialized_bytes_equal": semantic["serialized_bytes_equal"],
                "chat_template_sha256": template_sha256,
                "processor_class": tokenizer["processor_class"],
            },
            "arms": {
                "base": {
                    "parent": raw_before,
                    "adapter": zero_identity,
                    "adapter_config": zero_attestation["zero_config"],
                    "adapter_config_sha256": sha256_file(zero_config_path),
                    "served_model_name": "qwen35-27b-base-exact-lora",
                    "composite_sha256": base_composite,
                },
                "trained": {
                    "parent": parent_before,
                    "adapter": selected_before,
                    "adapter_config": zero_attestation["source_config"],
                    "adapter_config_sha256": sha256_file(trained_config_path),
                    "served_model_name": (
                        f"qwen35-browser-action-corrected-{artifact_sha[:12]}-exact-lora"
                    ),
                    "composite_sha256": trained_composite,
                },
            },
            "composite_pair_sha256": pair_sha256,
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
        published_manifest_path = output / manifest_path.name
        publish_json(manifest_path, manifest)
        manifest_receipt = {
            "schema": CORRECTION_FINALIZATION_RECEIPT_SCHEMA,
            "status": "ok",
            "campaign_digest": campaign.digest,
            "artifact_source_git_sha": artifact_sha,
            "manifest_path": str(published_manifest_path),
            "manifest_sha256": sha256_file(manifest_path),
            "composite_pair_sha256": pair_sha256,
            "base_composite_sha256": base_composite,
            "trained_composite_sha256": trained_composite,
            "continuation_receipt_sha256": inputs["receipt_sha256"],
            "selection_manifest_sha256": inputs["selection_sha256"],
            **(
                {
                    "adaptive_execution": inputs["selection"]["adaptive_execution"],
                    "v3_import_sha256": inputs["selection"]["v3_import"]["sha256"],
                    "escalation_allowlist_sha256": inputs["selection"][
                        "escalation_allowlist"
                    ]["sha256"],
                    "escalation_attempts": inputs["selection"]["escalation_attempts"],
                }
                if inputs["selection"]["schema"] == ADAPTIVE_SELECTION_SCHEMA
                else {}
            ),
            "selected_update": inputs["selected_update"],
        }
        publish_json(staging / "exact_lora_manifest_receipt.json", manifest_receipt)
        if output.exists() or output.is_symlink():
            raise ArtifactError("browser-action final output appeared during publication")
        staging.replace(output)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise

    manifest_path = output / "exact_lora_manifest.json"
    try:
        final_inputs = _verify_inputs(
            campaign,
            root=root,
            stage=stage,
            artifact_sha=artifact_sha,
            receipt_path=receipt_path,
            selection_path=selection_path,
        )
        if (
            final_inputs != inputs
            or component_identity(raw) != raw_before
            or component_identity(parent) != parent_before
            or component_identity(selected_adapter) != selected_before
            or read_json(manifest_path) != manifest
            or sha256_file(manifest_path)
            != read_json(output / "exact_lora_manifest_receipt.json").get("manifest_sha256")
            or component_identity(output / "base_zero_adapter")
            != manifest["arms"]["base"]["adapter"]
            or (output / "model").exists()
        ):
            raise ArtifactError("published browser-action exact-LoRA output failed verification")
    except BaseException:
        # The directory was created by this invocation and has not been handed
        # off successfully. Remove an invalid publication instead of leaving a
        # path that a waiter could mistake for a complete manifest.
        if output.is_dir() and not output.is_symlink():
            shutil.rmtree(output)
        raise
    return manifest
