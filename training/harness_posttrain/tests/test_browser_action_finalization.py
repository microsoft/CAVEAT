from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from harness_posttrain import browser_action_finalization as finalization
from harness_posttrain.artifacts import (
    ArtifactError,
    canonical_json,
    read_json,
    sha256_bytes,
    sha256_file,
)
from harness_posttrain.browser_action_continuation_receipt import CONTINUATION_RECEIPT_SCHEMA
from harness_posttrain.browser_action_selection import (
    _COMPLETION_BUDGET,
    _HARD_GATES,
    _RANK_ORDER,
    _SELECTOR_TRANSPORT_POLICY,
    _SINGLE_ATTEMPT_EXECUTION,
    ATTEMPT_RECEIPT_SCHEMA,
    EVIDENCE_SCHEMA,
    SELECTION_SCHEMA,
    _attempt_inventory,
    _attempt_receipt,
    _cache_inventory,
    _cache_name,
    _metrics_from_evidence,
    _request_execution_summary,
)
from harness_posttrain.browser_action_selection_v4 import (
    _ADAPTIVE_COUNTS,
    ADAPTIVE_SELECTION_SCHEMA,
)
from harness_posttrain.config import Campaign
from harness_posttrain.exact_lora import ZERO_ADAPTER_SCHEMA


def _write(path: Path, value: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, bytes):
        path.write_bytes(value)
    elif isinstance(value, str):
        path.write_text(value, encoding="utf-8")
    else:
        path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _evidence() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candidate in ("step20", "step22", "step24", "step26", "step28"):
        for index in range(64):
            rows.append(
                {
                    "schema": EVIDENCE_SCHEMA,
                    "candidate": candidate,
                    "kind": "contract",
                    "task_id": f"contract-{index}",
                    "request": {"row": index},
                    "request_sha256": sha256_bytes(canonical_json({"row": index}).encode()),
                    "response": {"ok": True},
                    "response_sha256": sha256_bytes(canonical_json({"ok": True}).encode()),
                    "completion_ceiling_bound": False,
                    "completion_budget": _COMPLETION_BUDGET,
                    "request_execution": _SINGLE_ATTEMPT_EXECUTION,
                    "selector_process_attempt_id": 1,
                    "scoring": {"syntax_valid": True, "semantic_exact": True},
                }
            )
        for transition in (
            "continue-incomplete",
            "checkpoint-complete",
            "repair-rejected",
            "act-after-approval",
        ):
            for index in range(8):
                request = {"transition": transition, "row": index}
                response = {"ok": True, "transition": transition}
                rows.append(
                    {
                        "schema": EVIDENCE_SCHEMA,
                        "candidate": candidate,
                        "kind": "browser_action",
                        "task_id": f"action-{index}",
                        "transition": transition,
                        "request": request,
                        "request_sha256": sha256_bytes(canonical_json(request).encode()),
                        "response": response,
                        "response_sha256": sha256_bytes(canonical_json(response).encode()),
                        "completion_ceiling_bound": False,
                        "completion_budget": _COMPLETION_BUDGET,
                        "request_execution": _SINGLE_ATTEMPT_EXECUTION,
                        "selector_process_attempt_id": 1,
                        "scoring": {
                            "transition_exact": True,
                            "premature_checkpoint_or_purchase": False,
                        },
                    }
                )
    return rows


def _selection_tree(
    tmp_path: Path, campaign: Campaign
) -> tuple[Path, Path, Path, dict[str, dict[str, Any]], dict[str, Any]]:
    root = tmp_path / ("a" * 40)
    stage = root / "browser_action_correction" / ("b" * 40)
    parent = root / "selected/merged"
    parent.mkdir(parents=True)
    inventory = {
        f"step{update}": {
            "name": f"step{update}",
            "update": update,
            "path": str((stage / f"adapters/step{update}").resolve()),
            "files": 2,
            "bytes": update,
            "tree_sha256": str(update // 2)[-1] * 64,
            "receipt_tree_sha256": str(update // 2)[-1] * 64,
            "adapter_config_sha256": "c" * 64,
            "stable_marker_sha256": "d" * 64,
        }
        for update in (20, 22, 24, 26, 28)
    }
    bindings = {"continuation_training_receipt_sha256": "e" * 64}

    raw_manifest = _write(root / "corpus/raw/manifest.json", {"sealed": True})
    split_manifest = _write(
        root / "corpus/splits/manifest.json", {"manifest_body_sha256": "f" * 64}
    )
    contracts = _write(root / "corpus/raw/selection_contract_tasks.jsonl", "{}\n")
    checkpoints = _write(root / "corpus/raw/selection_checkpoint_tasks.jsonl", "{}\n")
    inputs = {
        "raw_manifest": str(raw_manifest.resolve()),
        "raw_manifest_sha256": sha256_file(raw_manifest),
        "split_manifest": str(split_manifest.resolve()),
        "split_manifest_sha256": sha256_file(split_manifest),
        "split_manifest_body_sha256": "f" * 64,
        "selection_contract_tasks": str(contracts.resolve()),
        "selection_contract_tasks_sha256": sha256_file(contracts),
        "selection_checkpoint_tasks": str(checkpoints.resolve()),
        "selection_checkpoint_tasks_sha256": sha256_file(checkpoints),
        "contract_tasks": 64,
        "checkpoint_tasks": 8,
        "source": "procedural",
        "split": "selection",
        "amazon_tasks": 0,
    }
    evidence = _evidence()
    evidence_path = stage / "selection/raw_evidence.jsonl"
    _write(
        evidence_path,
        "".join(canonical_json(row) + "\n" for row in evidence),
    )
    selection_dir = stage / "selection"
    cache_dir = selection_dir / "response_cache"
    for row in evidence:
        _write(cache_dir / _cache_name(row), row)
    attempt_dir = selection_dir / "attempts/attempt-0001"
    start_body = {
        "schema": ATTEMPT_RECEIPT_SCHEMA,
        "status": "started",
        "attempt_id": 1,
        "process_pid": 101,
        "cached_before_attempt": 0,
        "expected_total": 480,
        "transport_policy": _SELECTOR_TRANSPORT_POLICY,
    }
    _write(
        attempt_dir / "start.json",
        {
            **start_body,
            "start_body_sha256": sha256_bytes(canonical_json(start_body).encode()),
        },
    )
    _write(
        attempt_dir / "receipt.json",
        _attempt_receipt(
            attempt_id=1,
            status="complete",
            cache_before=0,
            completed=480,
            expected=480,
            failure=None,
            started_process_pid=101,
        ),
    )
    _write(attempt_dir / "server/command.json", {"command": ["vllm"]})
    _write(attempt_dir / "server/vllm.log", "server stopped normally\n")
    attempts = _attempt_inventory(selection_dir, evidence)
    metrics = _metrics_from_evidence(evidence, set(inventory))
    selected_name = "step20"
    body = {
        "schema": SELECTION_SCHEMA,
        "status": "complete",
        "campaign_digest": campaign.digest,
        "selection_split": "procedural_validation",
        "amazon_data_used": False,
        "inputs": inputs,
        "training_bindings": bindings,
        "base_model": str(parent.resolve()),
        "num_gpus": 4,
        "candidate_count": 5,
        "candidate_inventory": inventory,
        "hard_gates": _HARD_GATES,
        "rank_order": list(_RANK_ORDER),
        "passing_candidates": sorted(inventory),
        "candidates": metrics,
        "selected": {
            "name": selected_name,
            "update": 20,
            "adapter": inventory[selected_name]["path"],
            "adapter_tree_sha256": inventory[selected_name]["tree_sha256"],
            "reason": "highest_ranked_hard_gate_pass",
            "metrics": metrics[selected_name],
        },
        "fallback": "step20",
        "raw_evidence": {
            "path": str(evidence_path.resolve()),
            "sha256": sha256_file(evidence_path),
            "rows": 480,
        },
        "request_counts_per_candidate": {
            "contract": 64,
            "continue-incomplete": 8,
            "checkpoint-complete": 8,
            "repair-rejected": 8,
            "act-after-approval": 8,
            "total": 96,
        },
        "server_attempts": attempts,
        "response_cache": _cache_inventory(cache_dir, evidence),
        "completion_ceiling_bound": False,
        "completion_budget": _COMPLETION_BUDGET,
        "request_execution_summary": _request_execution_summary(evidence, attempts),
    }
    manifest = {**body, "manifest_body_sha256": sha256_bytes(canonical_json(body).encode())}
    selection = _write(stage / "selection/selected_checkpoint.json", manifest)
    return root, stage, selection, inventory, bindings


def test_correction_layout_accepts_only_reviewed_adaptive_selection(
    tmp_path: Path,
) -> None:
    root = (tmp_path / ("a" * 40)).resolve()
    stage = root / "browser_action_correction" / ("b" * 40)
    receipt = stage / "training/training_receipt.json"
    retry = stage / "selection_nonbinding_v4/selected_checkpoint.json"
    assert finalization._correction_layout(root, receipt, retry) == (  # noqa: SLF001
        stage.resolve(),
        "b" * 40,
    )
    with pytest.raises(ArtifactError, match="outside"):
        finalization._correction_layout(  # noqa: SLF001
            root,
            receipt,
            stage / "selection_nonbinding_v2/selected_checkpoint.json",
        )
    with pytest.raises(ArtifactError, match="outside"):
        finalization._correction_layout(  # noqa: SLF001
            root,
            receipt,
            stage / "selection_nonbinding_v3/selected_checkpoint.json",
        )
    with pytest.raises(ArtifactError, match="outside"):
        finalization._correction_layout(  # noqa: SLF001
            root,
            receipt,
            stage / "selection_unreviewed/selected_checkpoint.json",
        )


def test_selection_gate_recomputes_evidence_and_rejects_unbounded_update(
    tmp_path: Path, campaign: Campaign
) -> None:
    root, stage, selection_path, inventory, bindings = _selection_tree(tmp_path, campaign)
    parent = (root / "selected/merged").resolve()
    result = finalization._verify_selection(  # noqa: SLF001
        campaign,
        root=root.resolve(),
        stage=stage.resolve(),
        selection_path=selection_path.resolve(),
        parent=parent,
        inventory=inventory,
        bindings=bindings,
    )
    assert result["selected"]["update"] == 20

    tampered = read_json(selection_path)
    tampered["selected"]["update"] = 21
    body = dict(tampered)
    body.pop("manifest_body_sha256")
    tampered["manifest_body_sha256"] = sha256_bytes(canonical_json(body).encode())
    _write(selection_path, tampered)
    with pytest.raises(ArtifactError, match="bounded selection"):
        finalization._verify_selection(  # noqa: SLF001
            campaign,
            root=root.resolve(),
            stage=stage.resolve(),
            selection_path=selection_path.resolve(),
            parent=parent,
            inventory=inventory,
            bindings=bindings,
        )


def test_selection_gate_rejects_any_hidden_retry_or_generation_reset(
    tmp_path: Path, campaign: Campaign
) -> None:
    root, stage, selection_path, inventory, bindings = _selection_tree(tmp_path, campaign)
    evidence_path = selection_path.parent / "raw_evidence.jsonl"
    evidence = [json.loads(line) for line in evidence_path.read_text(encoding="utf-8").splitlines()]
    evidence[0]["request_execution"] = {
        **_SINGLE_ATTEMPT_EXECUTION,
        "attempt_count": 2,
        "retry_count": 1,
        "client_generation_reset_count": 1,
        "completed_single_attempt": False,
    }
    _write(evidence_path, "".join(canonical_json(row) + "\n" for row in evidence))
    manifest = read_json(selection_path)
    manifest["raw_evidence"]["sha256"] = sha256_file(evidence_path)
    body = dict(manifest)
    body.pop("manifest_body_sha256")
    manifest["manifest_body_sha256"] = sha256_bytes(canonical_json(body).encode())
    _write(selection_path, manifest)

    with pytest.raises(ArtifactError, match="raw evidence"):
        finalization._verify_selection(  # noqa: SLF001
            campaign,
            root=root.resolve(),
            stage=stage.resolve(),
            selection_path=selection_path.resolve(),
            parent=(root / "selected/merged").resolve(),
            inventory=inventory,
            bindings=bindings,
        )


def test_selection_gate_rejects_cache_content_that_differs_from_evidence(
    tmp_path: Path, campaign: Campaign
) -> None:
    root, stage, selection_path, inventory, bindings = _selection_tree(tmp_path, campaign)
    manifest = read_json(selection_path)
    descriptor = manifest["response_cache"]["files"][0]
    cache_path = selection_path.parent / "response_cache" / descriptor["name"]
    cached = read_json(cache_path)
    cached["scoring"] = {**cached["scoring"], "semantic_exact": False}
    _write(cache_path, cached)
    descriptor["sha256"] = sha256_file(cache_path)
    body = dict(manifest)
    body.pop("manifest_body_sha256")
    manifest["manifest_body_sha256"] = sha256_bytes(canonical_json(body).encode())
    _write(selection_path, manifest)

    with pytest.raises(ArtifactError, match="cache differs from raw evidence"):
        finalization._verify_selection(  # noqa: SLF001
            campaign,
            root=root.resolve(),
            stage=stage.resolve(),
            selection_path=selection_path.resolve(),
            parent=(root / "selected/merged").resolve(),
            inventory=inventory,
            bindings=bindings,
        )


def test_selection_gate_rejects_attempt_receipt_that_claims_hidden_retry(
    tmp_path: Path, campaign: Campaign
) -> None:
    root, stage, selection_path, inventory, bindings = _selection_tree(tmp_path, campaign)
    manifest = read_json(selection_path)
    attempt = manifest["server_attempts"][0]
    receipt_path = Path(attempt["receipt"])
    receipt = read_json(receipt_path)
    receipt["automatic_request_retries"] = 1
    body = dict(receipt)
    body.pop("receipt_body_sha256")
    receipt["receipt_body_sha256"] = sha256_bytes(canonical_json(body).encode())
    _write(receipt_path, receipt)
    attempt["receipt_sha256"] = sha256_file(receipt_path)
    manifest_body = dict(manifest)
    manifest_body.pop("manifest_body_sha256")
    manifest["manifest_body_sha256"] = sha256_bytes(
        canonical_json(manifest_body).encode()
    )
    _write(selection_path, manifest)

    with pytest.raises(ArtifactError, match="process-attempt policy"):
        finalization._verify_selection(  # noqa: SLF001
            campaign,
            root=root.resolve(),
            stage=stage.resolve(),
            selection_path=selection_path.resolve(),
            parent=(root / "selected/merged").resolve(),
            inventory=inventory,
            bindings=bindings,
        )


def test_continuation_receipt_gate_rehashes_every_direct_input(
    tmp_path: Path, campaign: Campaign
) -> None:
    root = tmp_path / ("a" * 40)
    artifact_sha = "b" * 40
    stage = root / "browser_action_correction" / artifact_sha
    parent = root / "selected/merged"
    provenance = _write(parent / "merge_provenance.json", {"status": "ok"})
    paths = {
        "plan_sha256": _write(stage / "training/plan.json", {}),
        "config_sha256": _write(stage / "training/browser_action_continuation.toml", "x"),
        "curriculum_manifest_sha256": _write(stage / "curriculum/manifest.json", {}),
        "curriculum_data_sha256": _write(stage / "curriculum/train.jsonl", "{}\n"),
        "prime_manifest_sha256": _write(stage / "prime/manifest.json", {}),
        "prime_parquet_sha256": _write(stage / "prime/train.parquet", b"parquet"),
    }
    source_paths = {
        "refinement_receipt_sha256": _write(root / "refinement/training_receipt.json", {}),
        "post_sft_receipt_sha256": _write(root / "post_sft_receipt.json", {}),
        "refinement_plan_sha256": _write(root / "refinement/config/plan.json", {}),
        "refinement_config_sha256": _write(root / "refinement/config/refinement.toml", "x"),
        "refinement_dataset_manifest_sha256": _write(root / "refinement/prime/manifest.json", {}),
    }
    receipt = {
        "schema": CONTINUATION_RECEIPT_SCHEMA,
        "status": "ok",
        "stage": "browser_action_continuation",
        "campaign_digest": campaign.digest,
        "artifact_source_git_sha": artifact_sha,
        "optimizer_updates": 28,
        "new_optimizer_updates": 8,
        "checkpoint_updates": [22, 24, 26, 28],
        "parent_model": str(parent.resolve()),
        "parent_merge_provenance_sha256": sha256_file(provenance),
        **{key: sha256_file(path) for key, path in paths.items()},
        "source": {
            "campaign_root": str(root.resolve()),
            "verified_unchanged_before_and_after": True,
            **{key: sha256_file(path) for key, path in source_paths.items()},
        },
        "checkpoints": [{"update": update} for update in (22, 24, 26, 28)],
    }
    receipt_path = _write(stage / "training/training_receipt.json", receipt)
    assert (
        finalization._verify_receipt_hashes(  # noqa: SLF001
            campaign,
            root=root.resolve(),
            stage=stage.resolve(),
            receipt_path=receipt_path.resolve(),
            artifact_sha=artifact_sha,
        )["status"]
        == "ok"
    )
    paths["plan_sha256"].write_text("changed", encoding="utf-8")
    with pytest.raises(ArtifactError, match="continuation plan hash changed"):
        finalization._verify_receipt_hashes(  # noqa: SLF001
            campaign,
            root=root.resolve(),
            stage=stage.resolve(),
            receipt_path=receipt_path.resolve(),
            artifact_sha=artifact_sha,
        )


def _inventory(*, all_zero: bool) -> dict[str, Any]:
    return {
        "tensors": {
            "base_model.q_proj.lora_A.weight": {
                "shape": [64, 4],
                "dtype": "bfloat16",
                "elements": 256,
                "bytes": 512,
            },
            "base_model.q_proj.lora_B.weight": {
                "shape": [4, 64],
                "dtype": "bfloat16",
                "elements": 256,
                "bytes": 512,
            },
        },
        "tensor_count": 2,
        "element_count": 512,
        "byte_count": 1024,
        "nonzero_elements": 0 if all_zero else 7,
        "all_zero": all_zero,
        "metadata": {"format": "pt"},
        "inventory_sha256": "1" * 64,
    }


def _full_finalization_tree(
    tmp_path: Path, campaign: Campaign
) -> tuple[Path, Path, Path, Path, Path, dict[str, Any]]:
    root = tmp_path / ("a" * 40)
    artifact_sha = "b" * 40
    stage = root / "browser_action_correction" / artifact_sha
    receipt_path = _write(
        stage / "training/training_receipt.json",
        {"schema": CONTINUATION_RECEIPT_SCHEMA, "status": "ok"},
    )
    selection_dir = stage / "selection_nonbinding_v4"
    adaptive_summary = {
        **_ADAPTIVE_COUNTS,
        "generation_attempts_lower_bound": 490,
        "aggregate_zero_generation_resets_claimed": False,
        "outcome_blind_escalation": True,
    }
    descriptors = {}
    v3 = stage / "selection_nonbinding_v3"
    v3_sources = {
        "source_start": _write(v3 / "attempts/attempt-0001/start.json", {}),
        "source_failed_receipt": _write(v3 / "attempts/attempt-0001/receipt.json", {}),
        "source_server_command": _write(
            v3 / "attempts/attempt-0001/server/command.json", []
        ),
        "source_server_log": _write(
            v3 / "attempts/attempt-0001/server/vllm.log", "failed\n"
        ),
    }
    cache_files = []
    for index in range(470):
        cache_file = _write(v3 / "response_cache" / f"row-{index:04d}.json", {})
        cache_files.append(
            {"name": cache_file.name, "sha256": sha256_file(cache_file)}
        )
    provenance = {
        "source_directory": str(v3.resolve()),
        "files": cache_files,
        **{name: str(path.resolve()) for name, path in v3_sources.items()},
        **{
            f"{name}_sha256": sha256_file(path)
            for name, path in v3_sources.items()
        },
    }
    for field, name, content in (
        ("v3_import", "v3_import_provenance.json", provenance),
        ("escalation_allowlist", "escalation_allowlist.json", {"field": "allowlist"}),
    ):
        artifact = _write(selection_dir / name, content)
        descriptors[field] = {
            "path": str(artifact.resolve()),
            "sha256": sha256_file(artifact),
        }
    selection_body = {
        "schema": ADAPTIVE_SELECTION_SCHEMA,
        "status": "complete",
        "adaptive_execution": adaptive_summary,
        "escalation_attempts": [
            {
                "attempt_id": 1,
                "status": "complete",
                "submitted": 10,
                "accepted": 10,
            }
        ],
        **descriptors,
    }
    attempt_dir = selection_dir / "escalation_attempts/attempt-0001"
    attempt_start = _write(attempt_dir / "start.json", {})
    attempt_receipt = _write(attempt_dir / "receipt.json", {})
    attempt_command = _write(attempt_dir / "server/command.json", [])
    attempt_log = _write(attempt_dir / "server/vllm.log", "complete\n")
    selection_body["escalation_attempts"][0].update(
        {
            "start": str(attempt_start.resolve()),
            "start_sha256": sha256_file(attempt_start),
            "receipt": str(attempt_receipt.resolve()),
            "receipt_sha256": sha256_file(attempt_receipt),
            "server_command": str(attempt_command.resolve()),
            "server_command_sha256": sha256_file(attempt_command),
            "server_log": str(attempt_log.resolve()),
            "server_log_sha256": sha256_file(attempt_log),
        }
    )
    selection_record = {
        **selection_body,
        "manifest_body_sha256": sha256_bytes(canonical_json(selection_body).encode()),
    }
    selection_path = _write(selection_dir / "selected_checkpoint.json", selection_record)
    parent = root / "selected/merged"
    _write(parent / "config.json", {"architectures": ["Qwen3_5ForCausalLM"]})
    _write(parent / "model.safetensors", b"parent")
    _write(parent / "tokenizer.json", {"tokenizer": "same"})
    _write(parent / "merge_provenance.json", {"status": "ok"})
    raw = root / "raw" / campaign.model["revision"]
    _write(raw / "config.json", {"architectures": ["Qwen3_5ForCausalLM"]})
    _write(raw / "model.safetensors", b"raw")
    _write(raw / "tokenizer.json", {"tokenizer": "same"})
    smoke = _write(root / "smoke/smoke_report.json", {"status": "ok"})
    adapter = stage / "training/prime_output/weights/step_24/lora_adapters"
    _write(
        adapter / "adapter_config.json",
        {
            "base_model_name_or_path": str(parent.resolve()),
            "r": 64,
            "lora_alpha": 128,
            "target_modules": ["q_proj"],
            "bias": "none",
        },
    )
    _write(adapter / "adapter_model.safetensors", b"trained")
    inputs = {
        "receipt": {"schema": CONTINUATION_RECEIPT_SCHEMA, "status": "ok"},
        "receipt_sha256": sha256_file(receipt_path),
        "selection": {
            **selection_record,
            "status": "complete",
        },
        "selection_sha256": sha256_file(selection_path),
        "parent": parent.resolve(),
        "inventory": {},
        "bindings": {},
        "selected_name": "step24",
        "selected_update": 24,
        "selected_adapter": adapter.resolve(),
    }
    return root, stage, receipt_path, selection_path, raw, {"inputs": inputs, "smoke": smoke}


def test_finalizer_atomically_publishes_corrected_manifest_and_validator(
    tmp_path: Path, campaign: Campaign, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, stage, receipt, selection, raw, state = _full_finalization_tree(tmp_path, campaign)
    inputs = state["inputs"]
    monkeypatch.setattr(finalization, "_verify_inputs", lambda *_args, **_kwargs: inputs)
    monkeypatch.setattr(
        finalization, "_verified_raw_base", lambda *_args, **_kwargs: state["smoke"]
    )
    tokenizer_sha = sha256_file(root / "selected/merged/tokenizer.json")
    monkeypatch.setattr(
        finalization,
        "_tokenizer_attestation",
        lambda *_args: {
            "processor_class": "transformers.Qwen3_5Processor",
            "semantic": {
                "shared_tokenizer_json_sha256": tokenizer_sha,
                "raw_tokenizer_json_sha256": sha256_file(raw / "tokenizer.json"),
                "semantic_sha256": "3" * 64,
                "serialized_bytes_equal": True,
                "chat_template_sha256": "4" * 64,
            },
        },
    )

    def zero_builder(source: Path, raw_base: Path, output: Path) -> dict[str, Any]:
        output.mkdir(parents=True)
        source_config = read_json(source / "adapter_config.json")
        zero_config = copy.deepcopy(source_config)
        zero_config["base_model_name_or_path"] = str(raw_base.resolve())
        _write(output / "adapter_config.json", zero_config)
        _write(output / "adapter_model.safetensors", b"zero")
        return {
            "schema": ZERO_ADAPTER_SCHEMA,
            "status": "ok",
            "source_adapter": str(source),
            "zero_adapter": str(output),
            "source_config": source_config,
            "zero_config": zero_config,
            "source_tensor_inventory": _inventory(all_zero=False),
            "zero_tensor_inventory": _inventory(all_zero=True),
        }

    monkeypatch.setattr(finalization, "create_zero_adapter", zero_builder)
    monkeypatch.setattr(
        finalization,
        "_lora_delta_attestation",
        lambda _adapter: {
            "pair_count": 1,
            "nonzero_delta_pairs": 1,
            "pairs": [{"module": "base_model.q_proj", "nonzero": True}],
        },
    )
    sources_before = {
        path: path.read_bytes()
        for component in (raw, inputs["parent"], inputs["selected_adapter"])
        for path in component.rglob("*")
        if path.is_file()
    }

    manifest = finalization.finalize_browser_action_correction(
        campaign,
        campaign_root=root,
        continuation_receipt=receipt,
        selection_manifest=selection,
        raw_base=raw,
    )

    output = stage / "final"
    assert manifest["selected_checkpoint"]["update"] == 24
    assert manifest["bf16_weights_merged"] is False
    assert manifest["arms"]["base"]["served_model_name"] == "qwen35-27b-base-exact-lora"
    assert manifest["arms"]["trained"]["served_model_name"] == (
        "qwen35-browser-action-corrected-bbbbbbbbbbbb-exact-lora"
    )
    assert manifest["zero_adapter_attestation"]["zero_adapter"] == str(
        (output / "base_zero_adapter").resolve()
    )
    assert manifest["structural_attestation"]["zero_noop"]["exact"] is True
    assert manifest["structural_attestation"]["trained_nonzero"]["nonzero_delta_pairs"] == 1
    assert not (output / "model").exists()
    assert sources_before == {path: path.read_bytes() for path in sources_before}
    launch = finalization.validate_browser_action_serving_manifest(
        output / "exact_lora_manifest.json", arm="trained"
    )
    assert launch["parent"] == str(inputs["parent"])
    assert launch["adapter"] == str(inputs["selected_adapter"])
    assert launch["served_model_name"] == manifest["arms"]["trained"]["served_model_name"]
    with pytest.raises(ArtifactError, match="create-only"):
        finalization.finalize_browser_action_correction(
            campaign,
            campaign_root=root,
            continuation_receipt=receipt,
            selection_manifest=selection,
            raw_base=raw,
        )
    (raw / "model.safetensors").write_bytes(b"tampered raw arm")
    with pytest.raises(ArtifactError, match="base component bytes changed"):
        finalization.validate_browser_action_serving_manifest(
            output / "exact_lora_manifest.json", arm="trained"
        )


def test_finalizer_cleans_staging_when_structural_gate_fails(
    tmp_path: Path, campaign: Campaign, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, stage, receipt, selection, raw, state = _full_finalization_tree(tmp_path, campaign)
    monkeypatch.setattr(finalization, "_verify_inputs", lambda *_args, **_kwargs: state["inputs"])
    monkeypatch.setattr(
        finalization, "_verified_raw_base", lambda *_args, **_kwargs: state["smoke"]
    )
    monkeypatch.setattr(
        finalization,
        "_tokenizer_attestation",
        lambda *_args: {
            "processor_class": "processor",
            "semantic": {
                "shared_tokenizer_json_sha256": "1" * 64,
                "raw_tokenizer_json_sha256": "2" * 64,
                "semantic_sha256": "3" * 64,
                "serialized_bytes_equal": True,
                "chat_template_sha256": "4" * 64,
            },
        },
    )

    def zero_builder(_source: Path, _raw: Path, output: Path) -> dict[str, Any]:
        output.mkdir(parents=True)
        _write(output / "adapter_config.json", {})
        _write(output / "adapter_model.safetensors", b"zero")
        return {"schema": ZERO_ADAPTER_SCHEMA, "status": "ok"}

    monkeypatch.setattr(finalization, "create_zero_adapter", zero_builder)
    with pytest.raises(ArtifactError, match="did not prove"):
        finalization.finalize_browser_action_correction(
            campaign,
            campaign_root=root,
            continuation_receipt=receipt,
            selection_manifest=selection,
            raw_base=raw,
        )
    assert not (stage / "final").exists()
    assert not list(stage.glob(".final.*"))


def test_parameterized_serving_wrapper_is_dp4_and_syntax_valid() -> None:
    script = Path(__file__).parents[1] / "scripts/serve_browser_action_evaluation_arm.sh"
    subprocess.run(["bash", "-n", str(script)], check=True)
    text = script.read_text(encoding="utf-8")
    assert "validate_browser_action_serving_manifest" in text
    assert "--data-parallel-size 4" in text
    assert "--api-server-count 4" in text
    assert 'manifest="${1:?' in text
    assert 'arm="${2:?' in text


def test_v4_selector_wrapper_binds_failed_v3_and_uses_adaptive_entrypoint() -> None:
    script = (
        Path(__file__).parents[1]
        / "scripts/select_finalize_browser_action_correction_v4.sh"
    )
    subprocess.run(["bash", "-n", str(script)], check=True)
    text = script.read_text(encoding="utf-8")
    assert '[[ ! -L "$output" ]]' in text
    assert '[[ -e "$output" && ! -d "$output" ]]' in text
    assert '[[ -d "$v3" && ! -L "$v3" ]]' in text
    assert "select-browser-action-checkpoint-v4" in text
    assert "--v3-selection-dir \"$v3\"" in text
    assert "selection_nonbinding_v4" in text


def test_v4_launch_wrapper_uses_immutable_image_and_four_gpus() -> None:
    script = Path(__file__).parents[1] / "scripts/launch_browser_action_selection_v4.sh"
    subprocess.run(["bash", "-n", str(script)], check=True)
    text = script.read_text(encoding="utf-8")
    assert "@sha256:" in text
    assert "GPUS_PER_NODE=4" in text
    assert "B200_PRIORITY=p0" in text
    assert "selection_nonbinding_v4" in text
    assert "select_finalize_browser_action_correction_v4.sh" in text
