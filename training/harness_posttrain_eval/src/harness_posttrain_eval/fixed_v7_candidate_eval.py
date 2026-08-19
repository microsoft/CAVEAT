#!/usr/bin/env python3
"""Create-only, outcome-blind fixed-v7 Amazon candidate evaluation.

The evaluation reuses the exact raw and step-20 controls frozen by the fixed-v6
prospective protocol.  Both fresh candidate category bundles are rendered and
bound together before either category is executed.  Marketplace outcomes are
first opened by ``finalize-category`` after execution; no score can change the
already-rendered office-chair candidate.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping

from harness_posttrain_eval.batch import audit_launch_manifest
from harness_posttrain_eval.common import (
    IntegrityError,
    canonical_bytes,
    read_json,
    sha256_bytes,
    sha256_file,
    write_json_create_only,
    write_text_create_only,
)
from harness_posttrain_eval.corrected_eval import _causal_spec
from harness_posttrain_eval.launcher import exact_lora_composite_sha256
from harness_posttrain_eval.manifest import verify_manifest
from harness_posttrain_eval.observations import convert_results


PROTOCOL_SCHEMA = "harness-posttrain-eval.fixed-v7-repair-step24-candidate-protocol.v1"
ENDPOINT_SCHEMA = "harness-posttrain-eval.fixed-v7-repair-step24-endpoint.v1"
BUNDLE_SCHEMA = "harness-posttrain-eval.fixed-v7-candidate-bundle.v1"
FREEZE_SCHEMA = "harness-posttrain-eval.fixed-v7-joint-evaluation-freeze.v1"
BINDING_SCHEMA = "harness-posttrain-eval.fixed-v7-category-binding.v1"
REPORT_SCHEMA = "harness-posttrain-eval.fixed-v7-category-report.v1"
TRANSFER_SCHEMA = "harness-posttrain-eval.fixed-v7-transfer-authorization.v1"
SYNTHESIS_PLAN_SCHEMA = "harness-posttrain-eval.fixed-v7-synthesis-plan.v1"
SYNTHESIS_REPORT_SCHEMA = "harness-posttrain-eval.fixed-v7-synthesis-report.v1"
MISSING_CONTROLS_SCHEMA = "harness-posttrain-eval.fixed-v7-missing-controls.v1"
FROZEN_PROTOCOL_FILE_SHA256 = (
    "3004e7cd98505956a95caea5457d9289adefd5c099219e6c9d428ff65e2c78ba"
)

SOURCE_PROTOCOL_FILE_SHA256 = (
    "0c47f7a9a78d02669e092660e17387f3ee45002bcabc8daccefeaa6d25fc5e55"
)
SOURCE_PROTOCOL_BODY_SHA256 = (
    "d4a2c6ecd169905af2d57b973eb669fe9a7e516a92428cdf1a4833c0bc9c10cd"
)
SOURCE_CONTROLS_FILE_SHA256 = (
    "fa49b2f4fad0d3b049eb77e1b21f4454d66e41b10a3ab71b60b28abb46d8d15f"
)
SOURCE_CONTROLS_BODY_SHA256 = (
    "827c5135651e8de1ae432f70b273f770773d9be5303300f2801a03d4ce34665b"
)
LABELS_FILE_SHA256 = "2688ae03694dde4103a75b7f45d640c2f811ec54fb1eca7aa2e3a3bcf6363553"
LABELS = {
    "laptop": "same_task_adaptation_replay",
    "office_chair": "untouched_category_generalization_confirmation",
}
CATEGORIES = tuple(LABELS)
VARIANTS = ("graded", "graded3", "graded4", "mixed")
CONDITIONS = {"clean": 4, "combined": 8}
EXPECTED_MODEL_BASE_URL = "http://127.0.0.1:18500/v1"
EXPECTED_MODEL_API_KEY = "env:HARNESS_POSTTRAIN_API_KEY"
MAX_CONCURRENCY = 12
REPAIR_EXECUTOR_GIT_SHA = "6cf2d5a0154ff644f6766b03648663bdbed10ec8"
REPLAY_EXECUTOR_GIT_SHA = "7ceed1104fea9807a8acd3d3cc0503dfb208c498"
REPLAY_VALIDATOR_GIT_SHA = "86e7f2cab46dedcb2b44b7fe0931d1ed60fc8dc1"
REPAIR_FANOUT_FILE_SHA256 = (
    "a560ee78f2c3f433501b3ce9bd2902894329f41c7732d7192c0bb471e9657383"
)
REPAIR_FANOUT_BODY_SHA256 = (
    "368e67b55cd2fe8eb5379f4af1366125fcc6204d0b9107644a23c8477bc1f0f1"
)
REPAIR_TRAINING_RECEIPT_FILE_SHA256 = (
    "85d5c779c8fb1ab4a1a87d4e4f4ab9bd7809675622bfe845a0632b2ab6ae4bf1"
)
REPAIR_TRAINING_RECEIPT_BODY_SHA256 = (
    "aa60d14b36e2bd5ae3cdab34cc74665ed883a03e59384f1174a17c624235fb1e"
)
EXPECTED_EXECUTION_VARIANT_COUNTS = {"graded": 8, "mixed": 8}
EXPECTED_VALID_VARIANT_COUNTS = {"graded": 7, "mixed": 6}
EXPECTED_ROLE_VARIANT_COUNTS = {
    "graded": {
        "cart_cleanup": 3,
        "cart_navigation": 6,
        "checkout": 5,
        "place_order": 4,
    },
    "mixed": {
        "cart_cleanup": 3,
        "cart_navigation": 5,
        "checkout": 3,
        "place_order": 5,
    },
}
EXPECTED_EXCLUDED_SEQUENCE_REASONS = {
    "amazon-r00-repair-repair-v2-7ceed-r1-graded-6": "truncated_proxy_completion",
    "amazon-r00-repair-repair-v2-7ceed-r1-mixed-1": "no_exact_wire_critical_target",
    "amazon-r00-repair-repair-v2-7ceed-r1-mixed-2": "terminal_policy_violation",
}

SYNTHESIS_ANALYSIS_SPEC = {
    "category_primary_claims": {
        "laptop": {
            "label": LABELS["laptop"],
            "scientific_role": "same_task_development_confirmation",
        },
        "office_chair": {
            "label": LABELS["office_chair"],
            "scientific_role": "untouched_category_generalization_confirmation",
        },
    },
    "conditions": {"combined": 8, "clean": 4},
    "metrics": {
        "primary": "strict_binary",
        "secondary": "preservation_strict",
    },
    "arms": ["raw", "step20", "fixed_v7"],
    "paired_effects": ["fixed_v7_minus_raw", "fixed_v7_minus_step20"],
    "exact_inference": {
        "scope": "separately_within_each_category_condition_and_metric",
        "comparison": "fixed_v7_minus_raw",
        "test": "one_sided_exact_sign_test",
        "null_positive_probability": 0.5,
        "zero_differences": "excluded",
        "tail": "P[Binomial(nonzero_pairs,0.5)>=positive_pairs]",
        "multiplicity_adjustment": "none_descriptive_small_n_confirmation",
        "decision_threshold": None,
    },
    "pooled_24_cell_summary": {
        "role": "descriptive_only",
        "equal_cell_weighting": True,
        "inference_performed": False,
        "selection_or_gate_use": False,
        "substitute_for_office_chair_claim": False,
    },
    "decision_policy": {
        "candidate_selection_from_scores": False,
        "retuning_from_scores": False,
        "retraining_from_scores": False,
        "office_chair_launch_score_independent": True,
        "threshold_gate": None,
    },
}

EXPECTED_ENDPOINT_TOP_LEVEL = {
    "schema",
    "status",
    "outcome_blind",
    "artifacts",
    "candidate",
    "model_spec",
    "training",
    "runtime",
    "api_evidence",
    "labels",
    "receipt_sha256",
}
EXPECTED_CANDIDATE_FIELDS = {
    "name",
    "update",
    "parent_tree_sha256",
    "adapter_tree_sha256",
    "adapter_config_sha256",
    "tokenizer_json_sha256",
    "chat_template_sha256",
    "dtype",
    "composite_sha256",
    "served_model_name",
}
EXPECTED_MODEL_FIELDS = {
    "name",
    "deployment",
    "provider",
    "api_key",
    "base_url",
    "vision",
    "extra",
}


def valid_repair_fanout(receipt: Mapping[str, Any]) -> bool:
    """Validate the exact immutable pooled real-state repair cohort."""

    inventory = receipt.get("inventory")
    excluded = receipt.get("excluded_executions")
    if not isinstance(inventory, list) or not isinstance(excluded, list):
        return False
    all_rows = [*inventory, *excluded]
    sequences = [str(row.get("sequence_id") or "") for row in all_rows]
    nonces = [str(row.get("environment_nonce") or "") for row in all_rows]
    ports = [row.get("proxy_port") for row in all_rows]
    excluded_reasons = {
        str(row.get("sequence_id") or ""): str(row.get("reason") or "")
        for row in excluded
    }
    return (
        receipt.get("schema") == "harness-distill.amazon-r00-repair-fanout-receipt.v4"
        and receipt.get("status") == "complete"
        and receipt.get("scientific_label")
        == "same_task_laptop_r00_pooled_exact_target_repair_fanout"
        and receipt.get("run_id") == "repair-v2-7ceed-r1"
        and receipt.get("executor_git_sha") == REPLAY_EXECUTOR_GIT_SHA
        and receipt.get("validator_git_sha") == REPLAY_VALIDATOR_GIT_SHA
        and receipt.get("max_concurrency") == 16
        and receipt.get("retry_count") == 0
        and receipt.get("topup_count") == 0
        and receipt.get("execution_count") == 16
        and receipt.get("valid_replay_count") == 13
        and receipt.get("excluded_execution_count") == 3
        and receipt.get("execution_variant_counts") == EXPECTED_EXECUTION_VARIANT_COUNTS
        and receipt.get("valid_variant_counts") == EXPECTED_VALID_VARIANT_COUNTS
        and receipt.get("excluded_sequence_reasons")
        == EXPECTED_EXCLUDED_SEQUENCE_REASONS
        and receipt.get("targets_per_variant_role_required") == 2
        and receipt.get("critical_role_variant_counts") == EXPECTED_ROLE_VARIANT_COUNTS
        and receipt.get("critical_role_variant_unique_target_counts")
        == EXPECTED_ROLE_VARIANT_COUNTS
        and receipt.get("base_port") == 34500
        and receipt.get("ports") == list(range(34500, 34516))
        and receipt.get("laptop_r00_used") is True
        and receipt.get("laptop_r01_used") is False
        and receipt.get("office_chair_used") is False
        and len(inventory) == 13
        and len(excluded) == 3
        and excluded_reasons == EXPECTED_EXCLUDED_SEQUENCE_REASONS
        and len(sequences) == len(set(sequences)) == 16
        and len(nonces) == len(set(nonces)) == 16
        and len(ports) == len(set(ports)) == 16
        and set(ports) == set(range(34500, 34516))
    )


EXPECTED_TRAINING_FIELDS = {
    "job",
    "job_uid",
    "job_spec_sha256",
    "job_labels_sha256",
    "node",
    "image_id",
    "pod",
    "pod_uid",
}
EXPECTED_RUNTIME_FIELDS = {
    "job",
    "job_uid",
    "job_spec_sha256",
    "job_labels_sha256",
    "pod",
    "pod_uid",
    "node",
    "image_id",
    "container_command",
    "container_args",
    "tunnel_argv",
    "tunnel_pid",
    "tunnel_process_start_ticks",
    "tunnel_supervisor_pid",
    "tunnel_supervisor_process_start_ticks",
    "tunnel_status_sha256",
}
EXPECTED_API_EVIDENCE_FIELDS = {
    "models_file_sha256",
    "canary_file_sha256",
    "served_models",
    "finish_reason",
}
ARTIFACT_BODY_FIELDS = {
    "training_release_descriptor": (
        "harness-posttrain.browser-action-next-iteration.one-update-sft-release.v1",
        "release_sha256",
    ),
    "training_receipt": (
        "harness-distill.amazon-r00-repair-sft-training-receipt.v1",
        "receipt_body_sha256",
    ),
    "fanout_receipt": (
        "harness-distill.amazon-r00-repair-fanout-receipt.v4",
        "receipt_body_sha256",
    ),
    "serve_release_descriptor": (
        "harness-posttrain.browser-action-fixed-v7-repair-step24-serve-release.v1",
        "release_sha256",
    ),
}


def _self_hash(value: Mapping[str, Any], field: str, label: str) -> str:
    core = {key: item for key, item in value.items() if key != field}
    digest = sha256_bytes(canonical_bytes(core))
    if value.get(field) != digest:
        raise IntegrityError(f"{label} has an invalid {field}")
    return digest


def _sha(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise IntegrityError(f"{label} is not a lowercase SHA-256")
    return value


def _existing_regular_file(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise IntegrityError(f"{label} is absent or a symlink: {path}")
    return path.resolve()


def _expected_cells(category: str) -> set[tuple[Any, ...]]:
    return {
        (category, variant, condition, repetition)
        for variant in VARIANTS
        for condition, repetitions in (("clean", (0,)), ("combined", (0, 1)))
        for repetition in repetitions
    }


def _read_labels(path: Path) -> dict[str, Any]:
    path = _existing_regular_file(path, "fixed-v7 labels")
    if sha256_file(path) != LABELS_FILE_SHA256:
        raise IntegrityError("fixed-v7 labels bytes changed")
    value = read_json(path)
    if (
        value.get("schema") != "harness-posttrain-eval.fixed-v7-labels.v1"
        or value.get("frozen_before_v7_training_or_inference") is not True
        or value.get("candidate_selection_from_amazon_outcomes") is not False
        or value.get("laptop", {}).get("label") != LABELS["laptop"]
        or value.get("office_chair", {}).get("label") != LABELS["office_chair"]
        or value.get("laptop", {}).get("conditions") != CONDITIONS
        or value.get("office_chair", {}).get("conditions") != CONDITIONS
        or value.get("office_chair", {}).get("training_contamination_allowed")
        is not False
    ):
        raise IntegrityError("fixed-v7 labels contract changed")
    return value


def _source_protocol(path: Path, *, require_control_results: bool) -> dict[str, Any]:
    path = _existing_regular_file(path, "source prospective protocol")
    if sha256_file(path) != SOURCE_PROTOCOL_FILE_SHA256:
        raise IntegrityError("source prospective protocol bytes changed")
    value = read_json(path)
    _self_hash(value, "protocol_sha256", "source prospective protocol")
    if (
        value.get("protocol_sha256") != SOURCE_PROTOCOL_BODY_SHA256
        or value.get("outcome_blind_freeze") is not True
        or value.get("outcomes_read_during_freeze") is not False
        or value.get("common_contract", {}).get("scaffold") != "browseruse-deliberative"
        or value.get("common_contract", {}).get("max_steps") != 4000
        or value.get("common_contract", {}).get("whole_run_timeout_seconds") != 36000
        or len(value.get("mappings", [])) != 24
    ):
        raise IntegrityError("source prospective protocol contract changed")
    seen: set[tuple[Any, ...]] = set()
    for mapping in value["mappings"]:
        _self_hash(mapping, "mapping_sha256", "source control mapping")
        cell = tuple(mapping.get("cell", ()))
        if cell in seen or cell not in _expected_cells(str(cell[0]) if cell else ""):
            raise IntegrityError(f"source prospective control cell changed: {cell}")
        seen.add(cell)
        for arm in ("raw", "step20"):
            descriptor = mapping.get(arm) or {}
            config = _existing_regular_file(
                Path(str(descriptor.get("config_path", ""))),
                f"{arm} control config",
            )
            if sha256_file(config) != descriptor.get("config_sha256"):
                raise IntegrityError(f"{arm} control config bytes changed: {cell}")
            if require_control_results:
                result = Path(str(descriptor.get("result_path", ""))).resolve()
                _existing_regular_file(result / "summary.json", f"{arm} summary")
                _existing_regular_file(result / "trajectory.json", f"{arm} trajectory")
    expected = set().union(*(_expected_cells(category) for category in CATEGORIES))
    if seen != expected:
        raise IntegrityError("source prospective control mappings are incomplete")
    return value


def missing_controls(protocol: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return only file-presence facts; never open marketplace outcome content."""

    missing: list[dict[str, Any]] = []
    for mapping in protocol.get("mappings", []):
        for arm in ("raw", "step20"):
            descriptor = mapping[arm]
            result = Path(str(descriptor["result_path"])).resolve()
            absent = [
                name
                for name in ("summary.json", "trajectory.json")
                if (result / name).is_symlink() or not (result / name).is_file()
            ]
            if absent:
                missing.append(
                    {
                        "cell": list(mapping["cell"]),
                        "arm": arm,
                        "run_id": descriptor["run_id"],
                        "config_sha256": descriptor["config_sha256"],
                        "result_path": str(result),
                        "missing_files": absent,
                    }
                )
    return sorted(missing, key=lambda row: (tuple(row["cell"]), row["arm"]))


def write_missing_controls_receipt(
    *, protocol: Mapping[str, Any], output: Path, phase: str
) -> dict[str, Any]:
    missing = missing_controls(protocol)
    core = {
        "schema": MISSING_CONTROLS_SCHEMA,
        "status": "blocked" if missing else "complete",
        "phase": phase,
        "outcome_content_read": False,
        "presence_only_audit": True,
        "protocol_sha256": protocol["protocol_sha256"],
        "missing_control_runs": len(missing),
        "missing": missing,
    }
    value = {**core, "receipt_sha256": sha256_bytes(canonical_bytes(core))}
    write_json_create_only(output, value)
    return value


def _source_controls(path: Path) -> dict[str, Any]:
    path = _existing_regular_file(path, "source prospective controls")
    if sha256_file(path) != SOURCE_CONTROLS_FILE_SHA256:
        raise IntegrityError("source prospective controls bytes changed")
    value = read_json(path)
    _self_hash(value, "controls_sha256", "source prospective controls")
    if (
        value.get("schema") != "harness-posttrain-eval.fixed-v6-controls.v1"
        or value.get("controls_sha256") != SOURCE_CONTROLS_BODY_SHA256
        or value.get("outcomes_read") is not False
        or value.get("selected_run_count") != 48
        or len(value.get("launches", [])) != 48
    ):
        raise IntegrityError("source prospective controls contract changed")
    return value


def freeze(arguments: argparse.Namespace) -> None:
    output = arguments.output.resolve()
    if output.exists() or output.is_symlink():
        raise IntegrityError(f"refusing to overwrite fixed-v7 protocol: {output}")
    labels_path = arguments.labels.resolve()
    source_path = arguments.source_protocol.resolve()
    controls_path = arguments.source_controls.resolve()
    labels = _read_labels(labels_path)
    source = _source_protocol(source_path, require_control_results=False)
    controls = _source_controls(controls_path)
    mappings = copy.deepcopy(source["mappings"])
    core = {
        "schema": PROTOCOL_SCHEMA,
        "status": "frozen_before_fixed_v7_endpoint_or_marketplace_inference",
        "outcome_blind_freeze": True,
        "outcomes_read_during_freeze": False,
        "source_protocol": {
            "path": str(source_path),
            "file_sha256": SOURCE_PROTOCOL_FILE_SHA256,
            "body_sha256": SOURCE_PROTOCOL_BODY_SHA256,
        },
        "source_controls": {
            "path": str(controls_path),
            "file_sha256": SOURCE_CONTROLS_FILE_SHA256,
            "body_sha256": SOURCE_CONTROLS_BODY_SHA256,
            "selected_run_count": len(controls["launches"]),
        },
        "labels": {
            "path": str(labels_path),
            "file_sha256": LABELS_FILE_SHA256,
            "laptop": LABELS["laptop"],
            "office_chair": LABELS["office_chair"],
        },
        "endpoint_contract": {
            "path": str(arguments.endpoint_receipt.resolve()),
            "schema": ENDPOINT_SCHEMA,
            "base_url": EXPECTED_MODEL_BASE_URL,
            "candidate_name": "step24-amazon-r00-repair-sft",
            "candidate_update": 24,
        },
        "candidate": {
            "arm": "fixed_v7_repair_step24",
            "single_candidate": True,
            "candidate_selection_from_evaluation_outcomes": False,
            "candidate_retraining_between_categories": False,
        },
        "common_contract": {
            "environment": "amazon",
            "scaffold": "browseruse-deliberative",
            "variants": list(VARIANTS),
            "conditions": CONDITIONS,
            "fresh_candidate_runs_per_category": 12,
            "max_steps": 4000,
            "whole_run_timeout_seconds": 36000,
            "max_concurrency": MAX_CONCURRENCY,
            "headline_metric": "strict_binary",
            "secondary_metric": "preservation_strict",
            "backstops_are_not_measured_constraints": True,
        },
        "categories": {
            "laptop": {
                "label": labels["laptop"]["label"],
                "claim": "same-task adaptation/replay only",
                "training_contamination_allowed": True,
            },
            "office_chair": {
                "label": labels["office_chair"]["label"],
                "claim": "untouched-category generalization confirmation",
                "training_contamination_allowed": False,
                "launch_gate": "laptop infrastructure-validity only; score-independent",
            },
        },
        "scientific_rules": {
            "render_and_bind_both_categories_before_any_candidate_run": True,
            "reuse_raw_and_step20_only_if_exact_config_hashes_match": True,
            "candidate_task_seed_causal_harness_and_limits_match_controls": True,
            "fresh_candidate_result_roots_required": True,
            "office_chair_never_used_for_training_or_model_selection": True,
            "laptop_score_cannot_select_retune_or_retrain_candidate": True,
            "control_or_candidate_infrastructure_invalid_runs_allowed": 0,
            "control_or_candidate_bound_runs_allowed": 0,
        },
        "mappings": mappings,
    }
    value = {**core, "protocol_sha256": sha256_bytes(canonical_bytes(core))}
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json_create_only(output, value)
    print(
        json.dumps({"status": "created", "protocol_sha256": value["protocol_sha256"]})
    )


def audit_protocol(
    path: Path, *, require_control_results: bool = False
) -> dict[str, Any]:
    path = _existing_regular_file(path, "fixed-v7 candidate protocol")
    if sha256_file(path) != FROZEN_PROTOCOL_FILE_SHA256:
        raise IntegrityError("fixed-v7 candidate protocol bytes changed")
    value = read_json(path)
    if value.get("schema") != PROTOCOL_SCHEMA:
        raise IntegrityError("fixed-v7 candidate protocol schema changed")
    _self_hash(value, "protocol_sha256", "fixed-v7 candidate protocol")
    source = value.get("source_protocol") or {}
    controls = value.get("source_controls") or {}
    labels = value.get("labels") or {}
    if (
        value.get("outcome_blind_freeze") is not True
        or value.get("outcomes_read_during_freeze") is not False
        or source.get("file_sha256") != SOURCE_PROTOCOL_FILE_SHA256
        or source.get("body_sha256") != SOURCE_PROTOCOL_BODY_SHA256
        or controls.get("file_sha256") != SOURCE_CONTROLS_FILE_SHA256
        or controls.get("body_sha256") != SOURCE_CONTROLS_BODY_SHA256
        or labels.get("file_sha256") != LABELS_FILE_SHA256
        or labels.get("laptop") != LABELS["laptop"]
        or labels.get("office_chair") != LABELS["office_chair"]
        or value.get("common_contract", {}).get("max_concurrency") != MAX_CONCURRENCY
        or value.get("scientific_rules", {}).get(
            "render_and_bind_both_categories_before_any_candidate_run"
        )
        is not True
    ):
        raise IntegrityError("fixed-v7 candidate protocol contract changed")
    source_value = _source_protocol(
        Path(str(source.get("path", ""))),
        require_control_results=require_control_results,
    )
    _source_controls(Path(str(controls.get("path", ""))))
    _read_labels(Path(str(labels.get("path", ""))))
    if value.get("mappings") != source_value.get("mappings"):
        raise IntegrityError("fixed-v7 control mappings differ from frozen source")
    return value


def _artifact_descriptor(value: Mapping[str, Any], name: str) -> dict[str, Any]:
    if set(value) != {"path", "file_sha256", "body_sha256"}:
        raise IntegrityError(f"fixed-v7 endpoint artifact descriptor changed: {name}")
    path = _existing_regular_file(Path(str(value["path"])), f"fixed-v7 {name}")
    if value["file_sha256"] != sha256_file(path):
        raise IntegrityError(f"fixed-v7 endpoint artifact bytes changed: {name}")
    artifact = read_json(path)
    schema, body_field = ARTIFACT_BODY_FIELDS[name]
    _self_hash(artifact, body_field, f"fixed-v7 {name}")
    if (
        artifact.get("schema") != schema
        or artifact.get(body_field) != value["body_sha256"]
    ):
        raise IntegrityError(f"fixed-v7 endpoint artifact semantics changed: {name}")
    return artifact


def audit_endpoint(path: Path) -> dict[str, Any]:
    path = _existing_regular_file(path, "fixed-v7 endpoint receipt")
    value = read_json(path)
    if set(value) != EXPECTED_ENDPOINT_TOP_LEVEL:
        raise IntegrityError("fixed-v7 endpoint top-level fields changed")
    if value.get("schema") != ENDPOINT_SCHEMA or value.get("status") != "ok":
        raise IntegrityError("fixed-v7 endpoint schema/status changed")
    _self_hash(value, "receipt_sha256", "fixed-v7 endpoint receipt")
    if value.get("outcome_blind") is not True:
        raise IntegrityError("fixed-v7 endpoint was not attested outcome-blind")
    artifacts = value.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != set(ARTIFACT_BODY_FIELDS):
        raise IntegrityError("fixed-v7 endpoint artifact inventory changed")
    artifact_values = {
        name: _artifact_descriptor(descriptor, name)
        for name, descriptor in artifacts.items()
    }
    training_receipt = artifact_values["training_receipt"]
    training_release = artifact_values["training_release_descriptor"]
    fanout_receipt = artifact_values["fanout_receipt"]
    serve_release = artifact_values["serve_release_descriptor"]
    release_candidate = serve_release.get("candidate") or {}
    release_receipt = serve_release.get("training_receipt") or {}
    release_fanout = serve_release.get("fanout_receipt") or {}
    if (
        training_release.get("status") != "released"
        or training_release.get("purpose")
        != "one_update_sft_from_r00_correction_replay"
        or training_release.get("source", {}).get("git_sha") != REPAIR_EXECUTOR_GIT_SHA
        or training_release.get("laptop_r01_outcomes_read") is not False
        or training_release.get("office_chair_outcomes_read") is not False
        or training_receipt.get("status") != "ok"
        or training_receipt.get("executor_git_sha") != REPAIR_EXECUTOR_GIT_SHA
        or training_receipt.get("replay_executor_git_sha") != REPLAY_EXECUTOR_GIT_SHA
        or training_receipt.get("replay_validator_git_sha") != REPLAY_VALIDATOR_GIT_SHA
        or training_receipt.get("fanout_receipt_sha256")
        != artifacts["fanout_receipt"]["file_sha256"]
        or training_receipt.get("fanout_receipt_body_sha256")
        != artifacts["fanout_receipt"]["body_sha256"]
        or training_receipt.get("dataset_manifest_sha256")
        != "2d2c9b1eac32fea137cec1852b854a8d34a4edcfa8982ac07c5a4d58a7987230"
        or training_receipt.get("dataset_sha256")
        != "4d614d80ae4db704b68118da00942b1584805e3e59a4a2cf0e1cee79117adc1c"
        or training_receipt.get("plan_file_sha256")
        != "9dae94e2081d37e74ed89be7a06c8c2a054d09ff37cb139b12d232a0c8aae6e2"
        or training_receipt.get("plan_body_sha256")
        != "fd5ffde22c4a6b587022f032cff629351ff88c0a930999d040a1802e964712fe"
        or training_receipt.get("scientific_label")
        != "same_task_laptop_r00_real_state_cart_repair_sft"
        or training_receipt.get("source_step") != 23
        or training_receipt.get("final_step") != 24
        or training_receipt.get("optimizer_updates") != 1
        or training_receipt.get("learning_rate") != 5e-7
        or training_receipt.get("assistant_tokens_only") is not True
        or training_receipt.get("candidate", {}).get("name")
        != "step24-amazon-r00-repair-sft"
        or training_receipt.get("candidate", {}).get("update") != 24
        or training_receipt.get("candidate", {}).get("tree_sha256")
        != "49e66d189603142232d0e4f1b3549d32b783ebed5fa4d3efd0e04af3197a5508"
        or training_receipt.get("final_dcp", {}).get("tree_sha256")
        != "018e4589edb0ee7dd95eb94511c6411458ba3d0c274fa8284fa17eaae10c2b35"
        or training_receipt.get("laptop_r00_used") is not True
        or training_receipt.get("laptop_r01_used") is not False
        or training_receipt.get("office_chair_used") is not False
        or not valid_repair_fanout(fanout_receipt)
        or serve_release.get("status") != "released"
        or serve_release.get("outcome_blind") is not True
        or serve_release.get("executor_git_sha") != REPAIR_EXECUTOR_GIT_SHA
        or release_receipt.get("file_sha256")
        != artifacts["training_receipt"]["file_sha256"]
        or release_receipt.get("body_sha256")
        != artifacts["training_receipt"]["body_sha256"]
        or release_fanout.get("file_sha256")
        != artifacts["fanout_receipt"]["file_sha256"]
        or release_fanout.get("body_sha256")
        != artifacts["fanout_receipt"]["body_sha256"]
        or artifacts["training_receipt"]["file_sha256"]
        != REPAIR_TRAINING_RECEIPT_FILE_SHA256
        or artifacts["training_receipt"]["body_sha256"]
        != REPAIR_TRAINING_RECEIPT_BODY_SHA256
        or artifacts["fanout_receipt"]["file_sha256"] != REPAIR_FANOUT_FILE_SHA256
        or artifacts["fanout_receipt"]["body_sha256"] != REPAIR_FANOUT_BODY_SHA256
        or release_fanout.get("executor_git_sha") != REPLAY_EXECUTOR_GIT_SHA
        or release_fanout.get("validator_git_sha") != REPLAY_VALIDATOR_GIT_SHA
        or release_fanout.get("execution_count") != 16
        or release_fanout.get("valid_replay_count") != 13
        or release_fanout.get("excluded_execution_count") != 3
        or release_candidate.get("name") != "step24-amazon-r00-repair-sft"
        or release_candidate.get("update") != 24
        or serve_release.get("scientific_flags")
        != {
            "laptop_r00_used": True,
            "laptop_r01_used": False,
            "office_chair_used": False,
        }
    ):
        raise IntegrityError("fixed-v7 release/training semantics changed")
    candidate = value.get("candidate")
    if not isinstance(candidate, dict) or set(candidate) != EXPECTED_CANDIDATE_FIELDS:
        raise IntegrityError("fixed-v7 candidate identity fields changed")
    for field in EXPECTED_CANDIDATE_FIELDS - {
        "name",
        "update",
        "dtype",
        "served_model_name",
    }:
        _sha(candidate.get(field), f"fixed-v7 candidate {field}")
    expected_composite = exact_lora_composite_sha256(
        parent_tree_sha256=candidate["parent_tree_sha256"],
        adapter_tree_sha256=candidate["adapter_tree_sha256"],
        adapter_config_sha256=candidate["adapter_config_sha256"],
        tokenizer_json_sha256=candidate["tokenizer_json_sha256"],
        chat_template_sha256=candidate["chat_template_sha256"],
        dtype=candidate["dtype"],
    )
    if (
        candidate.get("name") != "step24-amazon-r00-repair-sft"
        or candidate.get("update") != 24
        or candidate.get("dtype") != "bfloat16"
        or candidate.get("composite_sha256") != expected_composite
        or candidate.get("adapter_config_sha256")
        != release_candidate.get("adapter_config_sha256")
        or candidate.get("parent_tree_sha256")
        != release_candidate.get("parent_tree_sha256")
        or candidate.get("adapter_tree_sha256")
        != release_candidate.get("adapter_tree_sha256")
        or candidate.get("served_model_name")
        != release_candidate.get("served_model_name")
    ):
        raise IntegrityError("fixed-v7 candidate identity is inconsistent")
    model = value.get("model_spec")
    if not isinstance(model, dict) or set(model) != EXPECTED_MODEL_FIELDS:
        raise IntegrityError("fixed-v7 endpoint model spec fields changed")
    if (
        model.get("name") != candidate["served_model_name"]
        or model.get("deployment") != candidate["served_model_name"]
        or model.get("provider") != "openai"
        or model.get("api_key") != EXPECTED_MODEL_API_KEY
        or model.get("base_url") != EXPECTED_MODEL_BASE_URL
        or model.get("vision") is not False
        or model.get("extra") != {"frequency_penalty": None}
    ):
        raise IntegrityError("fixed-v7 endpoint model spec changed")
    training = value.get("training")
    runtime = value.get("runtime")
    evidence = value.get("api_evidence")
    if not isinstance(training, dict) or set(training) != EXPECTED_TRAINING_FIELDS:
        raise IntegrityError("fixed-v7 endpoint training attestation fields changed")
    if not isinstance(runtime, dict) or set(runtime) != EXPECTED_RUNTIME_FIELDS:
        raise IntegrityError("fixed-v7 endpoint runtime attestation fields changed")
    if not isinstance(evidence, dict) or set(evidence) != EXPECTED_API_EVIDENCE_FIELDS:
        raise IntegrityError("fixed-v7 endpoint API evidence fields changed")
    for container, fields in (
        (training, ("job_spec_sha256", "job_labels_sha256")),
        (runtime, ("job_spec_sha256", "job_labels_sha256", "tunnel_status_sha256")),
        (evidence, ("models_file_sha256", "canary_file_sha256")),
    ):
        for field in fields:
            _sha(container.get(field), f"fixed-v7 endpoint {field}")
    if (
        not isinstance(training.get("job"), str)
        or not training["job"]
        or not isinstance(training.get("job_uid"), str)
        or not training["job_uid"]
        or not isinstance(training.get("node"), str)
        or not training["node"]
        or not isinstance(training.get("image_id"), str)
        or not training["image_id"]
        or not isinstance(runtime.get("job"), str)
        or not runtime["job"]
        or not isinstance(runtime.get("job_uid"), str)
        or not runtime["job_uid"]
        or not isinstance(runtime.get("pod_uid"), str)
        or not runtime["pod_uid"]
        or not isinstance(runtime.get("tunnel_argv"), list)
        or not runtime["tunnel_argv"]
        or not isinstance(evidence.get("served_models"), list)
        or not evidence["served_models"]
        or len(evidence["served_models"]) != len(set(evidence["served_models"]))
        or not all(isinstance(name, str) and name for name in evidence["served_models"])
        or candidate["served_model_name"] not in evidence["served_models"]
        or not set(evidence["served_models"]).issubset(
            {candidate["served_model_name"], "qwen35-exact-lora-parent"}
        )
        or evidence.get("finish_reason") != "stop"
        or value.get("labels") != LABELS
    ):
        raise IntegrityError("fixed-v7 runtime/API/label attestation changed")
    return value


def _derived_manifest(
    source: dict[str, Any], endpoint: dict[str, Any]
) -> dict[str, Any]:
    core = copy.deepcopy(
        {key: item for key, item in source.items() if key != "manifest_sha256"}
    )
    candidate = endpoint["candidate"]["composite_sha256"]
    controls = {
        source["model_contract"]["base_weight_sha256"],
        source["model_contract"]["trained_weight_sha256"],
    }
    if candidate in controls:
        raise IntegrityError("fixed-v7 candidate aliases a raw or step20 control")
    core["model_contract"]["trained_weight_sha256"] = candidate
    core["treatment_difference"] = (
        "The reused controls and fixed-v7 candidate differ only in exact model weight identity."
    )
    return {**core, "manifest_sha256": sha256_bytes(canonical_bytes(core))}


def _control_launch_by_id(controls: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result = {row["run_id"]: row for row in controls["launches"]}
    if len(result) != 48:
        raise IntegrityError("source control run IDs are not unique")
    return result


def _control_manifest(
    *,
    category: str,
    mappings: list[dict[str, Any]],
    controls: dict[str, Any],
    source_launch: dict[str, Any],
) -> dict[str, Any]:
    by_id = _control_launch_by_id(controls)
    launches: list[dict[str, Any]] = []
    for mapping in mappings:
        for source_arm, arm in (("raw", "base"), ("step20", "trained")):
            row = copy.deepcopy(by_id.get(mapping[source_arm]["run_id"]))
            if not row or row.get("arm") != arm:
                raise IntegrityError(f"exact {source_arm} control launch is absent")
            if (
                row.get("config") != mapping[source_arm]["config_path"]
                or row.get("config_sha256") != mapping[source_arm]["config_sha256"]
                or row.get("results") != mapping[source_arm]["result_path"]
            ):
                raise IntegrityError(f"exact {source_arm} control launch changed")
            launches.append(row)
    core = {
        "schema_version": 2,
        "schema": "harness-posttrain-eval.fixed-v7-reused-controls.v1",
        "execution_mode": "balanced_two_arm",
        "reuse_only_evidence": True,
        "category": category,
        "campaign_id": source_launch["campaign_id"],
        "matrix_sha256": source_launch["matrix_sha256"],
        "endpoint_manifest_sha256": source_launch["endpoint_manifest_sha256"],
        "results_root": source_launch["results_root"],
        "launches": sorted(launches, key=lambda row: row["run_id"]),
    }
    value = {**core, "launch_manifest_sha256": sha256_bytes(canonical_bytes(core))}
    audit_launch_manifest(value)
    return value


def _cell_from_candidate_run(run_id: str) -> tuple[Any, ...]:
    pieces = run_id.split("::")
    if (
        len(pieces) != 6
        or pieces[0] != "fixed_v7"
        or pieces[1] not in CATEGORIES
        or pieces[5] != "fixed_v7"
        or not pieces[4].startswith("r")
    ):
        raise IntegrityError(f"fixed-v7 candidate run ID changed: {run_id}")
    try:
        repetition = int(pieces[4][1:])
    except ValueError as exc:
        raise IntegrityError(f"fixed-v7 repetition changed: {run_id}") from exc
    return pieces[1], pieces[2], pieces[3], repetition


def _render_category(
    *,
    category: str,
    protocol: dict[str, Any],
    endpoint: dict[str, Any],
    source_launch: dict[str, Any],
    source_frozen: dict[str, Any],
    controls: dict[str, Any],
    stage_root: Path,
    final_root: Path,
    results_root: Path,
    base_port: int,
    python_executable: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    mappings = sorted(
        [mapping for mapping in protocol["mappings"] if mapping["cell"][0] == category],
        key=lambda mapping: tuple(mapping["cell"]),
    )
    if len(mappings) != 12:
        raise IntegrityError(f"fixed-v7 {category} mappings are incomplete")
    if base_port < 1024 or base_port + 11 > 65535:
        raise IntegrityError(f"fixed-v7 {category} storefront port band is invalid")
    endpoint_port = int(EXPECTED_MODEL_BASE_URL.rsplit(":", 1)[1].split("/", 1)[0])
    if endpoint_port in range(base_port, base_port + 12):
        raise IntegrityError("fixed-v7 model and storefront port bands overlap")
    bundle_stage = stage_root / "bundles" / category
    configs = bundle_stage / "configs"
    configs.mkdir(parents=True)
    bundle_final = final_root / "bundles" / category
    launches: list[dict[str, Any]] = []
    binding_mappings: list[dict[str, Any]] = []
    candidate_model = endpoint["model_spec"]
    derived_manifest = _derived_manifest(source_frozen, endpoint)
    for index, mapping in enumerate(mappings):
        cell = tuple(mapping["cell"])
        source_spec = read_json(Path(mapping["raw"]["config_path"]))
        repetition = int(cell[3])
        run_id = (
            f"fixed_v7::{category}::{cell[1]}::{cell[2]}::r{repetition:02d}::fixed_v7"
        )
        pair_id = f"fixed_v7::{category}::{cell[1]}::{cell[2]}::r{repetition:02d}"
        result = (
            results_root
            / category
            / (
                "amazon__browseruse-deliberative__fixed_v7__"
                f"{category}-{cell[1]}__{cell[2]}__{run_id.replace('::', '-')}"
            )
        )
        if result.exists() or result.is_symlink():
            raise IntegrityError(f"fixed-v7 candidate result is not fresh: {result}")
        spec = copy.deepcopy(source_spec)
        runtime = copy.deepcopy(source_spec["runtime_environment"])
        runtime["AGENTARENA_CACHE_NONCE"] = f"fixed_v7/{run_id}"
        audit = copy.deepcopy(source_spec["audit_contract"])
        audit.update(
            {
                "endpoint_manifest_sha256": endpoint["receipt_sha256"],
                "frozen_manifest_sha256": derived_manifest["manifest_sha256"],
                "fixed_v7_protocol_sha256": protocol["protocol_sha256"],
                "fixed_v7_endpoint_receipt_file_sha256": sha256_file(
                    Path(protocol["endpoint_contract"]["path"])
                ),
                "fixed_v7_endpoint_receipt_body_sha256": endpoint["receipt_sha256"],
                "fixed_v7_training_receipt_file_sha256": endpoint["artifacts"][
                    "training_receipt"
                ]["file_sha256"],
                "fixed_v7_serve_release_file_sha256": endpoint["artifacts"][
                    "serve_release_descriptor"
                ]["file_sha256"],
                "fixed_v7_candidate_composite_sha256": endpoint["candidate"][
                    "composite_sha256"
                ],
                "fixed_v7_evaluation_label": LABELS[category],
                "fixed_v7_labels_file_sha256": LABELS_FILE_SHA256,
                "fixed_v7_control_mapping_sha256": mapping["mapping_sha256"],
            }
        )
        spec.update(
            {
                "arm": "fixed_v7",
                "model": copy.deepcopy(candidate_model),
                "run_id": run_id,
                "pair_id": pair_id,
                "port": base_port + index,
                "out_dir": str(result),
                "runtime_environment": runtime,
                "audit_contract": audit,
            }
        )
        if (
            spec.get("max_steps") != mapping["max_steps"]
            or spec.get("run_timeout_seconds") != mapping["run_timeout_seconds"]
            or spec.get("scaffold") != mapping["scaffold"]
            or spec.get("block_seed") != mapping["block_seed"]
            or sha256_bytes(canonical_bytes(spec.get("task"))) != mapping["task_sha256"]
            or sha256_bytes(canonical_bytes(_causal_spec(spec)))
            != mapping["causal_config_sha256"]
            or audit.get("harness_sha256") != mapping["harness_sha256"]
            or audit.get("limit_contract_sha256") != mapping["limit_contract_sha256"]
        ):
            raise IntegrityError(f"fixed-v7 causal evaluation contract changed: {cell}")
        for arm in ("raw", "step20"):
            control_model = mapping[arm]["model"]
            if any(
                candidate_model[key] == control_model[key]
                for key in ("name", "deployment", "base_url")
            ):
                raise IntegrityError(f"fixed-v7 candidate aliases {arm}: {cell}")
        config_path = configs / f"{index:02d}_{run_id.replace('::', '-')}.json"
        write_json_create_only(config_path, spec)
        final_config = bundle_final / "configs" / config_path.name
        launch = {
            "run_id": run_id,
            "pair_id": pair_id,
            "arm": "fixed_v7",
            "port": base_port + index,
            "config": str(final_config),
            "config_sha256": sha256_file(config_path),
            "results": str(result),
            "argv": [
                python_executable,
                "-m",
                "harness_posttrain_eval.launch_one",
                "--spec",
                str(final_config),
            ],
            "environment": runtime,
            "audit_contract": audit,
        }
        launches.append(launch)
        mapping_core = {
            "cell": list(cell),
            "candidate_run_id": run_id,
            "candidate_config_sha256": launch["config_sha256"],
            "raw_run_id": mapping["raw"]["run_id"],
            "raw_config_sha256": mapping["raw"]["config_sha256"],
            "step20_run_id": mapping["step20"]["run_id"],
            "step20_config_sha256": mapping["step20"]["config_sha256"],
        }
        binding_mappings.append(
            {
                **mapping_core,
                "mapping_sha256": sha256_bytes(canonical_bytes(mapping_core)),
            }
        )
    launch_core = {
        "schema_version": 2,
        "schema": "harness-posttrain-eval.fixed-v7-candidate-launch.v1",
        "execution_mode": "single_arm_completion",
        "evaluation": LABELS[category],
        "campaign_id": source_launch["campaign_id"],
        "matrix_sha256": source_launch["matrix_sha256"],
        "endpoint_manifest_sha256": endpoint["receipt_sha256"],
        "protocol_sha256": protocol["protocol_sha256"],
        "category": category,
        "base_port": base_port,
        "results_root": str(results_root / category),
        "launches": launches,
    }
    launch = {
        **launch_core,
        "launch_manifest_sha256": sha256_bytes(canonical_bytes(launch_core)),
    }
    write_json_create_only(bundle_stage / "launch_manifest.json", launch)
    write_json_create_only(bundle_stage / "frozen_manifest.json", derived_manifest)
    control = _control_manifest(
        category=category,
        mappings=mappings,
        controls=controls,
        source_launch=source_launch,
    )
    write_json_create_only(bundle_stage / "control_launch_manifest.json", control)
    bundle_core = {
        "schema": BUNDLE_SCHEMA,
        "status": "rendered_before_any_fixed_v7_candidate_marketplace_run",
        "outcomes_read_during_render": False,
        "category": category,
        "label": LABELS[category],
        "protocol_sha256": protocol["protocol_sha256"],
        "endpoint_receipt_sha256": endpoint["receipt_sha256"],
        "launch_manifest_sha256": launch["launch_manifest_sha256"],
        "control_launch_manifest_sha256": control["launch_manifest_sha256"],
        "frozen_manifest_sha256": derived_manifest["manifest_sha256"],
        "run_count": 12,
        "conditions": CONDITIONS,
        "results_root": str(results_root / category),
    }
    bundle = {
        **bundle_core,
        "bundle_sha256": sha256_bytes(canonical_bytes(bundle_core)),
    }
    write_json_create_only(bundle_stage / "bundle.json", bundle)
    binding_core = {
        "schema": BINDING_SCHEMA,
        "status": "bound_before_any_fixed_v7_candidate_marketplace_run",
        "outcomes_read_during_binding": False,
        "category": category,
        "label": LABELS[category],
        "protocol_sha256": protocol["protocol_sha256"],
        "endpoint_receipt_sha256": endpoint["receipt_sha256"],
        "candidate_model": copy.deepcopy(candidate_model),
        "candidate_composite_sha256": endpoint["candidate"]["composite_sha256"],
        "candidate_launch_manifest_sha256": launch["launch_manifest_sha256"],
        "control_launch_manifest_sha256": control["launch_manifest_sha256"],
        "mappings": binding_mappings,
    }
    binding = {
        **binding_core,
        "binding_sha256": sha256_bytes(canonical_bytes(binding_core)),
    }
    binding_stage = stage_root / "bindings" / category
    binding_stage.mkdir(parents=True)
    write_json_create_only(binding_stage / "binding.json", binding)
    return bundle, binding


def render(arguments: argparse.Namespace) -> None:
    protocol = audit_protocol(
        arguments.protocol.resolve(), require_control_results=False
    )
    endpoint_path = arguments.endpoint_receipt.resolve()
    if endpoint_path != Path(protocol["endpoint_contract"]["path"]).resolve():
        raise IntegrityError(
            "fixed-v7 endpoint receipt path differs from frozen protocol"
        )
    endpoint = audit_endpoint(endpoint_path)
    source_preparation = arguments.source_preparation.resolve()
    source_launch = read_json(source_preparation / "run_bundle/launch_manifest.json")
    audit_launch_manifest(source_launch)
    source_frozen = read_json(source_preparation / "frozen_manifest.json")
    verify_manifest(source_frozen, root=arguments.repository_root.resolve())
    source_protocol = read_json(Path(protocol["source_protocol"]["path"]))
    if (
        source_launch.get("launch_manifest_sha256")
        != source_protocol["source"]["launch_manifest_sha256"]
    ):
        raise IntegrityError("source launch manifest differs from prospective protocol")
    controls = _source_controls(Path(protocol["source_controls"]["path"]))
    output = arguments.output_root.resolve()
    results_root = output / "run_results"
    if output.exists() or output.is_symlink():
        raise IntegrityError(f"refusing to overwrite fixed-v7 evaluation: {output}")
    if arguments.laptop_base_port + 12 > arguments.office_chair_base_port and (
        arguments.office_chair_base_port + 12 > arguments.laptop_base_port
    ):
        raise IntegrityError("fixed-v7 category storefront port bands overlap")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{output.name}.", dir=output.parent
    ) as temporary:
        stage = Path(temporary) / "evaluation"
        stage.mkdir()
        bundles: dict[str, dict[str, Any]] = {}
        bindings: dict[str, dict[str, Any]] = {}
        for category, port in (
            ("laptop", arguments.laptop_base_port),
            ("office_chair", arguments.office_chair_base_port),
        ):
            bundles[category], bindings[category] = _render_category(
                category=category,
                protocol=protocol,
                endpoint=endpoint,
                source_launch=source_launch,
                source_frozen=source_frozen,
                controls=controls,
                stage_root=stage,
                final_root=output,
                results_root=results_root,
                base_port=port,
                python_executable=arguments.python_executable,
            )
        freeze_core = {
            "schema": FREEZE_SCHEMA,
            "status": "both_categories_rendered_and_bound_before_any_candidate_run",
            "outcomes_read_during_joint_freeze": False,
            "protocol_path": str(arguments.protocol.resolve()),
            "protocol_file_sha256": sha256_file(arguments.protocol),
            "protocol_body_sha256": protocol["protocol_sha256"],
            "endpoint_receipt_path": str(endpoint_path),
            "endpoint_receipt_file_sha256": sha256_file(endpoint_path),
            "endpoint_receipt_body_sha256": endpoint["receipt_sha256"],
            "candidate_composite_sha256": endpoint["candidate"]["composite_sha256"],
            "labels_file_sha256": LABELS_FILE_SHA256,
            "categories": {
                category: {
                    "label": LABELS[category],
                    "bundle_sha256": bundles[category]["bundle_sha256"],
                    "binding_sha256": bindings[category]["binding_sha256"],
                    "launch_manifest_sha256": bundles[category][
                        "launch_manifest_sha256"
                    ],
                    "fresh_candidate_runs": 12,
                    "conditions": CONDITIONS,
                }
                for category in CATEGORIES
            },
            "office_chair_candidate_fixed_before_laptop_outcomes": True,
            "candidate_selection_or_retuning_after_laptop_forbidden": True,
        }
        joint = {
            **freeze_core,
            "evaluation_freeze_sha256": sha256_bytes(canonical_bytes(freeze_core)),
        }
        write_json_create_only(stage / "evaluation_freeze.json", joint)
        os.rename(stage, output)
    audit_evaluation(output, arguments.protocol.resolve(), endpoint_path)
    print(
        json.dumps(
            {
                "status": "created",
                "evaluation_freeze_sha256": joint["evaluation_freeze_sha256"],
                "candidate_runs": 24,
                "outcomes_read": False,
            }
        )
    )


def audit_evaluation(
    root: Path,
    protocol_path: Path,
    endpoint_path: Path,
    *,
    require_fresh_results: bool = True,
) -> dict[str, Any]:
    protocol = audit_protocol(protocol_path, require_control_results=False)
    endpoint = audit_endpoint(endpoint_path)
    joint = read_json(root / "evaluation_freeze.json")
    if joint.get("schema") != FREEZE_SCHEMA:
        raise IntegrityError("fixed-v7 joint evaluation freeze schema changed")
    _self_hash(joint, "evaluation_freeze_sha256", "fixed-v7 joint evaluation freeze")
    if (
        joint.get("outcomes_read_during_joint_freeze") is not False
        or joint.get("protocol_file_sha256") != sha256_file(protocol_path)
        or joint.get("protocol_body_sha256") != protocol["protocol_sha256"]
        or joint.get("endpoint_receipt_file_sha256") != sha256_file(endpoint_path)
        or joint.get("endpoint_receipt_body_sha256") != endpoint["receipt_sha256"]
        or joint.get("candidate_composite_sha256")
        != endpoint["candidate"]["composite_sha256"]
        or joint.get("office_chair_candidate_fixed_before_laptop_outcomes") is not True
    ):
        raise IntegrityError("fixed-v7 joint evaluation freeze changed")
    for category in CATEGORIES:
        bundle_root = root / "bundles" / category
        bundle = read_json(bundle_root / "bundle.json")
        _self_hash(bundle, "bundle_sha256", f"fixed-v7 {category} bundle")
        launch = read_json(bundle_root / "launch_manifest.json")
        control = read_json(bundle_root / "control_launch_manifest.json")
        audit_launch_manifest(launch)
        audit_launch_manifest(control)
        if control.get("reuse_only_evidence") is not True:
            raise IntegrityError(
                f"fixed-v7 {category} controls are not reuse-only evidence"
            )
        binding = read_json(root / "bindings" / category / "binding.json")
        _self_hash(binding, "binding_sha256", f"fixed-v7 {category} binding")
        if (
            len(launch.get("launches", [])) != 12
            or {row.get("arm") for row in launch["launches"]} != {"fixed_v7"}
            or launch.get("execution_mode") != "single_arm_completion"
            or len(control.get("launches", [])) != 24
            or len(binding.get("mappings", [])) != 12
            or binding.get("outcomes_read_during_binding") is not False
            or bundle.get("outcomes_read_during_render") is not False
            or bundle.get("label") != LABELS[category]
            or binding.get("label") != LABELS[category]
            or binding.get("candidate_composite_sha256")
            != endpoint["candidate"]["composite_sha256"]
            or binding.get("candidate_launch_manifest_sha256")
            != launch["launch_manifest_sha256"]
            or binding.get("control_launch_manifest_sha256")
            != control["launch_manifest_sha256"]
            or joint["categories"][category]["bundle_sha256"] != bundle["bundle_sha256"]
            or joint["categories"][category]["binding_sha256"]
            != binding["binding_sha256"]
        ):
            raise IntegrityError(f"fixed-v7 {category} bundle/binding changed")
        frozen = {
            tuple(mapping["cell"]): mapping
            for mapping in protocol["mappings"]
            if mapping["cell"][0] == category
        }
        seen: set[tuple[Any, ...]] = set()
        for row in launch["launches"]:
            spec = read_json(Path(row["config"]))
            cell = _cell_from_candidate_run(str(spec.get("run_id", "")))
            mapping = frozen.get(cell)
            audit = spec.get("audit_contract") or {}
            if (
                mapping is None
                or cell in seen
                or spec.get("model") != endpoint["model_spec"]
                or spec.get("max_steps") != mapping["max_steps"]
                or spec.get("run_timeout_seconds") != mapping["run_timeout_seconds"]
                or spec.get("block_seed") != mapping["block_seed"]
                or sha256_bytes(canonical_bytes(spec.get("task")))
                != mapping["task_sha256"]
                or sha256_bytes(canonical_bytes(_causal_spec(spec)))
                != mapping["causal_config_sha256"]
                or audit.get("harness_sha256") != mapping["harness_sha256"]
                or audit.get("limit_contract_sha256")
                != mapping["limit_contract_sha256"]
                or audit.get("fixed_v7_evaluation_label") != LABELS[category]
            ):
                raise IntegrityError(
                    f"fixed-v7 {category} candidate config changed: {cell}"
                )
            result = Path(row["results"]).resolve()
            if require_fresh_results and (result.exists() or result.is_symlink()):
                raise IntegrityError(
                    f"fixed-v7 candidate result is no longer fresh: {result}"
                )
            seen.add(cell)
        if seen != set(frozen):
            raise IntegrityError(f"fixed-v7 {category} candidate cells are incomplete")
        for mapping in binding["mappings"]:
            _self_hash(mapping, "mapping_sha256", f"fixed-v7 {category} paired mapping")
    return joint


def _jsonl(path: Path) -> list[dict[str, Any]]:
    path = _existing_regular_file(path, "fixed-v7 observations")
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise IntegrityError(f"invalid observation row {number}: {path}") from exc
        if not isinstance(value, dict):
            raise IntegrityError(f"non-object observation row {number}: {path}")
        rows.append(value)
    return rows


def _audit_conversion(
    path: Path, observations: Path, expected_runs: int
) -> dict[str, Any]:
    value = read_json(_existing_regular_file(path, "fixed-v7 conversion audit"))
    _self_hash(value, "conversion_audit_sha256", "fixed-v7 conversion audit")
    if (
        value.get("complete") is not True
        or value.get("expected_runs") != expected_runs
        or value.get("observation_rows") != expected_runs
        or value.get("infrastructure_invalid_runs") != 0
        or value.get("observations_sha256") != sha256_file(observations)
    ):
        raise IntegrityError("fixed-v7 conversion is incomplete or invalid")
    return value


def _valid_observation(row: dict[str, Any], config_sha256: str, label: str) -> None:
    audit = row.get("audit") or {}
    if (
        row.get("infrastructure_valid") is not True
        or audit.get("launch_config_sha256") != config_sha256
        or audit.get("recorded_scores_match_fresh") is not True
        or audit.get("safety_backstop_bound") is not False
        or audit.get("lossy_context_bound") is not False
        or audit.get("protocol_attempt_exhausted") is not False
        or audit.get("runtime_drift") is not False
    ):
        raise IntegrityError(
            f"{label} observation is invalid or bounded: {row.get('run_id')}"
        )


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _sign_pvalue(deltas: list[float]) -> float:
    nonzero = [value for value in deltas if value != 0]
    if not nonzero:
        return 1.0
    wins = sum(value > 0 for value in nonzero)
    return sum(math.comb(len(nonzero), k) for k in range(wins, len(nonzero) + 1)) / (
        2 ** len(nonzero)
    )


def _artifact_binding(path: Path, body_field: str) -> dict[str, Any]:
    value = read_json(_existing_regular_file(path, "fixed-v7 synthesis input"))
    _self_hash(value, body_field, f"fixed-v7 synthesis input {path.name}")
    return {
        "path": str(path.resolve()),
        "file_sha256": sha256_file(path),
        "body_sha256": value[body_field],
    }


def _synthesis_category_inventory(root: Path, category: str) -> dict[str, Any]:
    binding_path = root / "bindings" / category / "binding.json"
    candidate_launch_path = root / "bundles" / category / "launch_manifest.json"
    control_launch_path = root / "bundles" / category / "control_launch_manifest.json"
    binding = read_json(binding_path)
    _self_hash(binding, "binding_sha256", f"fixed-v7 {category} binding")
    candidate_launch = read_json(candidate_launch_path)
    control_launch = read_json(control_launch_path)
    audit_launch_manifest(candidate_launch)
    audit_launch_manifest(control_launch)
    mappings = binding.get("mappings")
    if not isinstance(mappings, list) or len(mappings) != 12:
        raise IntegrityError(f"fixed-v7 {category} synthesis mapping inventory changed")
    cells = [mapping.get("cell") for mapping in mappings if isinstance(mapping, dict)]
    if len(cells) != 12 or {
        tuple(cell) for cell in cells if isinstance(cell, list)
    } != (_expected_cells(category)):
        raise IntegrityError(f"fixed-v7 {category} synthesis cells changed")
    for mapping in mappings:
        _self_hash(mapping, "mapping_sha256", f"fixed-v7 {category} paired mapping")
    if (
        binding.get("candidate_launch_manifest_sha256")
        != candidate_launch.get("launch_manifest_sha256")
        or binding.get("control_launch_manifest_sha256")
        != control_launch.get("launch_manifest_sha256")
        or binding.get("outcomes_read_during_binding") is not False
    ):
        raise IntegrityError(f"fixed-v7 {category} synthesis launch binding changed")
    return {
        "label": LABELS[category],
        "binding": _artifact_binding(binding_path, "binding_sha256"),
        "candidate_launch_manifest": _artifact_binding(
            candidate_launch_path, "launch_manifest_sha256"
        ),
        "control_launch_manifest": _artifact_binding(
            control_launch_path, "launch_manifest_sha256"
        ),
        "mapping_count": 12,
        "mapping_inventory_sha256": sha256_bytes(canonical_bytes(mappings)),
        "cells_sha256": sha256_bytes(canonical_bytes(cells)),
        "planned_report_path": str(
            (root / "reports" / category / "report.json").resolve()
        ),
    }


def _synthesis_plan_core(
    *,
    evaluation_root: Path,
    protocol_path: Path,
    endpoint_path: Path,
    plan_path: Path,
    evaluator_git_sha: str,
) -> dict[str, Any]:
    root = evaluation_root.resolve()
    protocol = audit_protocol(protocol_path, require_control_results=False)
    endpoint = audit_endpoint(endpoint_path)
    joint = audit_evaluation(
        root,
        protocol_path,
        endpoint_path,
        require_fresh_results=False,
    )
    if re.fullmatch(r"[0-9a-f]{40}", evaluator_git_sha) is None:
        raise IntegrityError("fixed-v7 synthesis evaluator Git identity is invalid")
    freeze_path = root / "evaluation_freeze.json"
    categories = {
        category: _synthesis_category_inventory(root, category)
        for category in CATEGORIES
    }
    if any(
        categories[category]["binding"]["body_sha256"]
        != joint["categories"][category]["binding_sha256"]
        or categories[category]["candidate_launch_manifest"]["body_sha256"]
        != joint["categories"][category]["launch_manifest_sha256"]
        for category in CATEGORIES
    ):
        raise IntegrityError("fixed-v7 synthesis inputs differ from joint freeze")
    return {
        "schema": SYNTHESIS_PLAN_SCHEMA,
        "status": "preregistered_before_category_outcome_access",
        "outcomes_read_during_preregistration": False,
        "category_reports_present_during_preregistration": False,
        "evaluation_root": str(root),
        "preregistration_path": str(plan_path.resolve()),
        "evaluator_source": {
            "git_sha": evaluator_git_sha,
            "module_sha256": sha256_file(Path(__file__).resolve()),
        },
        "protocol": {
            "path": str(protocol_path.resolve()),
            "file_sha256": sha256_file(protocol_path),
            "body_sha256": protocol["protocol_sha256"],
        },
        "joint_evaluation_freeze": {
            "path": str(freeze_path.resolve()),
            "file_sha256": sha256_file(freeze_path),
            "body_sha256": joint["evaluation_freeze_sha256"],
        },
        "endpoint_receipt": {
            "path": str(endpoint_path.resolve()),
            "file_sha256": sha256_file(endpoint_path),
            "body_sha256": endpoint["receipt_sha256"],
            "candidate_composite_sha256": endpoint["candidate"]["composite_sha256"],
        },
        "categories": categories,
        "analysis_spec": SYNTHESIS_ANALYSIS_SPEC,
        "planned_synthesis_report_path": str(
            (root / "reports" / "synthesis" / "report.json").resolve()
        ),
    }


def preregister_synthesis(arguments: argparse.Namespace) -> None:
    root = arguments.evaluation_root.resolve()
    protocol_path = arguments.protocol.resolve()
    endpoint_path = arguments.endpoint_receipt.resolve()
    output = arguments.output.resolve()
    expected_output = root / "synthesis" / "preregistration.json"
    if output != expected_output:
        raise IntegrityError("fixed-v7 synthesis preregistration path is noncanonical")
    forbidden = [
        *(root / "reports" / category / "report.json" for category in CATEGORIES),
        root / "reports" / "synthesis" / "report.json",
    ]
    if any(path.exists() or path.is_symlink() for path in forbidden):
        raise IntegrityError(
            "fixed-v7 synthesis must be preregistered before any category report exists"
        )
    core = _synthesis_plan_core(
        evaluation_root=root,
        protocol_path=protocol_path,
        endpoint_path=endpoint_path,
        plan_path=output,
        evaluator_git_sha=arguments.evaluator_git_sha,
    )
    plan = {
        **core,
        "synthesis_plan_sha256": sha256_bytes(canonical_bytes(core)),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json_create_only(output, plan)
    print(
        json.dumps(
            {
                "status": "preregistered",
                "synthesis_plan_sha256": plan["synthesis_plan_sha256"],
                "outcomes_read": False,
            }
        )
    )


def audit_synthesis_plan(
    plan_path: Path,
    *,
    evaluation_root: Path,
    protocol_path: Path,
    endpoint_path: Path,
    evaluator_git_sha: str,
) -> dict[str, Any]:
    path = plan_path.resolve()
    root = evaluation_root.resolve()
    if path != root / "synthesis" / "preregistration.json":
        raise IntegrityError("fixed-v7 synthesis preregistration path changed")
    plan = read_json(_existing_regular_file(path, "fixed-v7 synthesis plan"))
    _self_hash(plan, "synthesis_plan_sha256", "fixed-v7 synthesis plan")
    expected = _synthesis_plan_core(
        evaluation_root=root,
        protocol_path=protocol_path.resolve(),
        endpoint_path=endpoint_path.resolve(),
        plan_path=path,
        evaluator_git_sha=evaluator_git_sha,
    )
    if {
        key: value for key, value in plan.items() if key != "synthesis_plan_sha256"
    } != expected:
        raise IntegrityError("fixed-v7 synthesis preregistration changed")
    return plan


def _convert_atomic(
    *,
    launch: dict[str, Any],
    frozen: dict[str, Any],
    repository_root: Path,
    output: Path,
) -> None:
    if output.exists() or output.is_symlink():
        raise IntegrityError(f"refusing to overwrite fixed-v7 conversion: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{output.name}.", dir=output.parent
    ) as temporary:
        stage = Path(temporary) / "conversion"
        stage.mkdir()
        convert_results(
            launch_manifest=launch,
            frozen_manifest=frozen,
            repository_root=repository_root,
            observations_output=stage / "observations.jsonl",
            audit_output=stage / "conversion_audit.json",
        )
        os.rename(stage, output)


def _summarize(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {"n": len(pairs)}
    for metric in ("strict_binary", "preservation_strict"):
        arms = {
            arm: [row[arm][metric] for row in pairs]
            for arm in ("raw", "step20", "fixed_v7")
        }
        summary[metric] = {
            **{f"{arm}_mean": _mean(values) for arm, values in arms.items()},
            "fixed_v7_minus_raw": _mean(
                [b - a for a, b in zip(arms["raw"], arms["fixed_v7"], strict=True)]
            ),
            "fixed_v7_minus_step20": _mean(
                [b - a for a, b in zip(arms["step20"], arms["fixed_v7"], strict=True)]
            ),
            "fixed_v7_vs_raw_one_sided_sign_p": _sign_pvalue(
                [b - a for a, b in zip(arms["raw"], arms["fixed_v7"], strict=True)]
            ),
        }
    return summary


def finalize_category(arguments: argparse.Namespace) -> None:
    root = arguments.evaluation_root.resolve()
    protocol_path = arguments.protocol.resolve()
    endpoint_path = arguments.endpoint_receipt.resolve()
    protocol = audit_protocol(protocol_path, require_control_results=False)
    endpoint = audit_endpoint(endpoint_path)
    joint = audit_evaluation(
        root,
        protocol_path,
        endpoint_path,
        require_fresh_results=False,
    )
    category = arguments.category
    missing = [row for row in missing_controls(protocol) if row["cell"][0] == category]
    if missing:
        blocked_root = root / "blocked" / category
        if blocked_root.exists() or blocked_root.is_symlink():
            raise IntegrityError(
                f"fixed-v7 {category} missing-control receipt already exists: {blocked_root}"
            )
        blocked_root.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=f".{category}.missing-controls.", dir=blocked_root.parent
        ) as temporary:
            stage = Path(temporary) / category
            stage.mkdir()
            receipt = write_missing_controls_receipt(
                protocol={
                    **protocol,
                    "mappings": [
                        mapping
                        for mapping in protocol["mappings"]
                        if mapping["cell"][0] == category
                    ],
                },
                output=stage / "missing_controls.json",
                phase="finalize_category",
            )
            os.rename(stage, blocked_root)
        raise IntegrityError(
            f"fixed-v7 {category} finalization blocked by "
            f"{receipt['missing_control_runs']} missing frozen control run(s); "
            f"receipt: {blocked_root / 'missing_controls.json'}"
        )
    bundle_root = root / "bundles" / category
    candidate_launch = read_json(bundle_root / "launch_manifest.json")
    control_launch = read_json(bundle_root / "control_launch_manifest.json")
    candidate_frozen = read_json(bundle_root / "frozen_manifest.json")
    source_preparation = arguments.source_preparation.resolve()
    control_frozen = read_json(source_preparation / "frozen_manifest.json")
    binding = read_json(root / "bindings" / category / "binding.json")
    _self_hash(binding, "binding_sha256", f"fixed-v7 {category} binding")
    for manifest in (candidate_launch, control_launch):
        audit_launch_manifest(manifest)
    if (
        len(candidate_launch.get("launches", [])) != 12
        or binding.get("candidate_launch_manifest_sha256")
        != candidate_launch.get("launch_manifest_sha256")
        or binding.get("control_launch_manifest_sha256")
        != control_launch.get("launch_manifest_sha256")
        or binding.get("candidate_composite_sha256")
        != endpoint["candidate"]["composite_sha256"]
        or binding.get("label") != LABELS[category]
    ):
        raise IntegrityError(f"fixed-v7 {category} finalization binding changed")
    status = read_json(arguments.executor_status.resolve())
    counts = status.get("counts") or {}
    if (
        status.get("launch_manifest_sha256")
        != candidate_launch["launch_manifest_sha256"]
        or status.get("success") is not True
        or counts.get("complete") != 12
        or sum(counts.values()) != 12
    ):
        raise IntegrityError(
            f"fixed-v7 {category} executor did not complete exactly 12 runs"
        )
    conversion_root = root / "conversions" / category
    report_root = root / "reports" / category
    if conversion_root.exists() or conversion_root.is_symlink():
        raise IntegrityError(
            f"refusing to overwrite fixed-v7 conversion: {conversion_root}"
        )
    if report_root.exists() or report_root.is_symlink():
        raise IntegrityError(f"refusing to overwrite fixed-v7 report: {report_root}")
    _convert_atomic(
        launch=control_launch,
        frozen=control_frozen,
        repository_root=arguments.repository_root.resolve(),
        output=conversion_root / "controls",
    )
    _convert_atomic(
        launch=candidate_launch,
        frozen=candidate_frozen,
        repository_root=arguments.repository_root.resolve(),
        output=conversion_root / "candidate",
    )
    control_observations = conversion_root / "controls/observations.jsonl"
    candidate_observations = conversion_root / "candidate/observations.jsonl"
    control_audit = _audit_conversion(
        conversion_root / "controls/conversion_audit.json", control_observations, 24
    )
    candidate_audit = _audit_conversion(
        conversion_root / "candidate/conversion_audit.json", candidate_observations, 12
    )
    controls = {row["run_id"]: row for row in _jsonl(control_observations)}
    candidates = {row["run_id"]: row for row in _jsonl(candidate_observations)}
    pairs: list[dict[str, Any]] = []
    for mapping in binding["mappings"]:
        raw = controls.get(mapping["raw_run_id"])
        step20 = controls.get(mapping["step20_run_id"])
        candidate = candidates.get(mapping["candidate_run_id"])
        if raw is None or step20 is None or candidate is None:
            raise IntegrityError(
                f"fixed-v7 paired observations are absent: {mapping['cell']}"
            )
        _valid_observation(raw, mapping["raw_config_sha256"], "raw")
        _valid_observation(step20, mapping["step20_config_sha256"], "step20")
        _valid_observation(candidate, mapping["candidate_config_sha256"], "fixed-v7")
        row: dict[str, Any] = {"cell": mapping["cell"]}
        for name, observation in (
            ("raw", raw),
            ("step20", step20),
            ("fixed_v7", candidate),
        ):
            row[name] = {
                "strict_binary": float(observation["strict_binary"]),
                "preservation_strict": float(observation["preservation_strict"]),
                "valid_transaction": bool(observation["valid_transaction"]),
                "result": observation.get("result"),
            }
        pairs.append(row)
    conditions = {
        condition: _summarize([row for row in pairs if row["cell"][2] == condition])
        for condition in ("combined", "clean")
    }
    report_core = {
        "schema": REPORT_SCHEMA,
        "status": "complete",
        "protocol_sha256": protocol["protocol_sha256"],
        "evaluation_freeze_sha256": joint["evaluation_freeze_sha256"],
        "binding_sha256": binding["binding_sha256"],
        "endpoint_receipt_sha256": endpoint["receipt_sha256"],
        "candidate_composite_sha256": endpoint["candidate"]["composite_sha256"],
        "category": category,
        "label": LABELS[category],
        "headline_metric": "strict_binary",
        "secondary_metric": "preservation_strict",
        "conditions": conditions,
        "pairs": pairs,
        "invariants": {
            "candidate_runs": 12,
            "reused_raw_runs": 12,
            "reused_step20_runs": 12,
            "clean_runs_per_arm": 4,
            "combined_runs_per_arm": 8,
            "infrastructure_invalid_runs": 0,
            "bound_runs": 0,
            "fresh_scores_recomputed": True,
            "identical_task_seed_causal_harness_and_limits": True,
            "both_candidate_categories_frozen_before_laptop_execution": True,
            "candidate_selection_or_retuning_from_laptop_score": False,
            "office_chair_training_contamination": False,
        },
        "conversion_audits": {
            "controls": control_audit["conversion_audit_sha256"],
            "candidate": candidate_audit["conversion_audit_sha256"],
        },
    }
    report = {
        **report_core,
        "report_sha256": sha256_bytes(canonical_bytes(report_core)),
    }
    report_root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{category}.", dir=report_root.parent
    ) as temporary:
        stage = Path(temporary) / category
        stage.mkdir()
        write_json_create_only(stage / "report.json", report)
        lines = [
            f"# Fixed-v7 {category.replace('_', ' ')}",
            "",
            f"Scientific label: `{LABELS[category]}`.",
            "",
            "| Condition | Runs/arm | Raw optimal-product rate | Step20 optimal-product rate | Fixed-v7 optimal-product rate | Fixed-v7 P* |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for condition in ("combined", "clean"):
            value = conditions[condition]
            binary = value["strict_binary"]
            pstar = value["preservation_strict"]
            lines.append(
                f"| {condition} | {value['n']} | {binary['raw_mean']:.1%} | "
                f"{binary['step20_mean']:.1%} | {binary['fixed_v7_mean']:.1%} | "
                f"{pstar['fixed_v7_mean']:.3f} |"
            )
        write_text_create_only(stage / "report.md", "\n".join(lines) + "\n")
        if category == "laptop":
            transfer_core = {
                "schema": TRANSFER_SCHEMA,
                "status": "authorized",
                "authorization_basis": "infrastructure_validity_only",
                "score_independent_authorization": True,
                "candidate_score_used_for_authorization": False,
                "candidate_selection_or_retuning_after_laptop": False,
                "protocol_sha256": protocol["protocol_sha256"],
                "evaluation_freeze_sha256": joint["evaluation_freeze_sha256"],
                "laptop_report_sha256": report["report_sha256"],
                "laptop_report_file_sha256": sha256_file(stage / "report.json"),
                "office_chair_binding_sha256": joint["categories"]["office_chair"][
                    "binding_sha256"
                ],
                "office_chair_launch_manifest_sha256": joint["categories"][
                    "office_chair"
                ]["launch_manifest_sha256"],
                "infrastructure_invalid_runs": 0,
                "bound_runs": 0,
            }
            transfer = {
                **transfer_core,
                "authorization_sha256": sha256_bytes(canonical_bytes(transfer_core)),
            }
            write_json_create_only(stage / "office_chair_authorization.json", transfer)
        os.rename(stage, report_root)
    print(
        json.dumps(
            {
                "status": "created",
                "category": category,
                "label": LABELS[category],
                "report_sha256": report["report_sha256"],
            }
        )
    )


def _audit_category_report_for_synthesis(
    *, root: Path, category: str, plan: Mapping[str, Any]
) -> dict[str, Any]:
    report_path = root / "reports" / category / "report.json"
    report = read_json(
        _existing_regular_file(report_path, f"fixed-v7 {category} report")
    )
    _self_hash(report, "report_sha256", f"fixed-v7 {category} report")
    category_plan = plan["categories"][category]
    binding = read_json(Path(category_plan["binding"]["path"]))
    _self_hash(binding, "binding_sha256", f"fixed-v7 {category} synthesis binding")
    expected_cells = [mapping["cell"] for mapping in binding["mappings"]]
    pairs = report.get("pairs")
    if not isinstance(pairs, list) or len(pairs) != 12:
        raise IntegrityError(
            f"fixed-v7 {category} synthesis pair inventory is incomplete"
        )
    observed_cells: list[list[Any]] = []
    for pair in pairs:
        if not isinstance(pair, dict) or set(pair) != {
            "cell",
            "raw",
            "step20",
            "fixed_v7",
        }:
            raise IntegrityError(f"fixed-v7 {category} synthesis pair is malformed")
        cell = pair.get("cell")
        if not isinstance(cell, list):
            raise IntegrityError(f"fixed-v7 {category} synthesis cell is malformed")
        observed_cells.append(cell)
        for arm in ("raw", "step20", "fixed_v7"):
            value = pair.get(arm)
            if (
                not isinstance(value, dict)
                or set(value)
                != {
                    "strict_binary",
                    "preservation_strict",
                    "valid_transaction",
                    "result",
                }
                or not isinstance(value["strict_binary"], (int, float))
                or isinstance(value["strict_binary"], bool)
                or float(value["strict_binary"]) not in {0.0, 1.0}
                or not isinstance(value["preservation_strict"], (int, float))
                or isinstance(value["preservation_strict"], bool)
                or not math.isfinite(float(value["preservation_strict"]))
                or not isinstance(value["valid_transaction"], bool)
            ):
                raise IntegrityError(
                    f"fixed-v7 {category} synthesis arm is malformed: {arm}"
                )
    expected_invariants = {
        "candidate_runs": 12,
        "reused_raw_runs": 12,
        "reused_step20_runs": 12,
        "clean_runs_per_arm": 4,
        "combined_runs_per_arm": 8,
        "infrastructure_invalid_runs": 0,
        "bound_runs": 0,
        "fresh_scores_recomputed": True,
        "identical_task_seed_causal_harness_and_limits": True,
        "both_candidate_categories_frozen_before_laptop_execution": True,
        "candidate_selection_or_retuning_from_laptop_score": False,
        "office_chair_training_contamination": False,
    }
    expected_conditions = {
        condition: _summarize([pair for pair in pairs if pair["cell"][2] == condition])
        for condition in ("combined", "clean")
    }
    if (
        report.get("schema") != REPORT_SCHEMA
        or report.get("status") != "complete"
        or report.get("protocol_sha256") != plan["protocol"]["body_sha256"]
        or report.get("evaluation_freeze_sha256")
        != plan["joint_evaluation_freeze"]["body_sha256"]
        or report.get("binding_sha256") != category_plan["binding"]["body_sha256"]
        or report.get("endpoint_receipt_sha256")
        != plan["endpoint_receipt"]["body_sha256"]
        or report.get("candidate_composite_sha256")
        != plan["endpoint_receipt"]["candidate_composite_sha256"]
        or report.get("category") != category
        or report.get("label") != LABELS[category]
        or report.get("headline_metric") != "strict_binary"
        or report.get("secondary_metric") != "preservation_strict"
        or report.get("invariants") != expected_invariants
        or observed_cells != expected_cells
        or report.get("conditions") != expected_conditions
        or set((report.get("conversion_audits") or {})) != {"controls", "candidate"}
    ):
        raise IntegrityError(f"fixed-v7 {category} synthesis report contract changed")
    return {
        "path": str(report_path.resolve()),
        "file_sha256": sha256_file(report_path),
        "body_sha256": report["report_sha256"],
        "pair_count": 12,
        "cells_sha256": sha256_bytes(canonical_bytes(observed_cells)),
        "pairs_sha256": sha256_bytes(canonical_bytes(pairs)),
        "pairs": pairs,
        "conditions": report["conditions"],
    }


def _descriptive_summary(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    summary = _summarize(pairs)
    for metric in ("strict_binary", "preservation_strict"):
        summary[metric].pop("fixed_v7_vs_raw_one_sided_sign_p")
    return summary


def _synthesis_report_core(
    *,
    plan_path: Path,
    evaluation_root: Path,
    protocol_path: Path,
    endpoint_path: Path,
    evaluator_git_sha: str,
) -> dict[str, Any]:
    root = evaluation_root.resolve()
    plan = audit_synthesis_plan(
        plan_path,
        evaluation_root=root,
        protocol_path=protocol_path,
        endpoint_path=endpoint_path,
        evaluator_git_sha=evaluator_git_sha,
    )
    audits = {
        category: _audit_category_report_for_synthesis(
            root=root, category=category, plan=plan
        )
        for category in CATEGORIES
    }
    pooled_pairs = [
        pair for category in CATEGORIES for pair in audits[category]["pairs"]
    ]
    primary_claims = {
        category: {
            "label": LABELS[category],
            "scientific_role": SYNTHESIS_ANALYSIS_SPEC["category_primary_claims"][
                category
            ]["scientific_role"],
            "category_report": {
                key: audits[category][key]
                for key in ("path", "file_sha256", "body_sha256")
            },
            "pair_inventory": {
                "count": audits[category]["pair_count"],
                "cells_sha256": audits[category]["cells_sha256"],
                "pairs_sha256": audits[category]["pairs_sha256"],
                "frozen_mapping_inventory_sha256": plan["categories"][category][
                    "mapping_inventory_sha256"
                ],
            },
            "conditions": audits[category]["conditions"],
            "inference": SYNTHESIS_ANALYSIS_SPEC["exact_inference"],
        }
        for category in CATEGORIES
    }
    return {
        "schema": SYNTHESIS_REPORT_SCHEMA,
        "status": "complete",
        "synthesis_plan": {
            "path": str(plan_path.resolve()),
            "file_sha256": sha256_file(plan_path),
            "body_sha256": plan["synthesis_plan_sha256"],
        },
        "protocol": plan["protocol"],
        "joint_evaluation_freeze": plan["joint_evaluation_freeze"],
        "endpoint_receipt": plan["endpoint_receipt"],
        "analysis_spec": plan["analysis_spec"],
        "primary_category_claims": primary_claims,
        "pooled_24_cell_descriptive": {
            "role": "descriptive_only",
            "pair_count": 24,
            "pairs_sha256": sha256_bytes(canonical_bytes(pooled_pairs)),
            "summary": _descriptive_summary(pooled_pairs),
            "inference_performed": False,
            "selection_or_gate_use": False,
            "substitute_for_office_chair_claim": False,
        },
        "invariants": {
            "category_specific_inference_is_primary": True,
            "category_reports_sealed_before_synthesis": True,
            "pooled_summary_is_descriptive_only": True,
            "candidate_selection_from_scores": False,
            "retuning_from_scores": False,
            "retraining_from_scores": False,
            "threshold_gate": None,
            "office_chair_launch_was_score_independent": True,
        },
    }


def finalize_synthesis(arguments: argparse.Namespace) -> None:
    root = arguments.evaluation_root.resolve()
    output = arguments.output.resolve()
    expected_output = root / "reports" / "synthesis" / "report.json"
    if output != expected_output:
        raise IntegrityError("fixed-v7 synthesis report path is noncanonical")
    core = _synthesis_report_core(
        plan_path=arguments.synthesis_plan.resolve(),
        evaluation_root=root,
        protocol_path=arguments.protocol.resolve(),
        endpoint_path=arguments.endpoint_receipt.resolve(),
        evaluator_git_sha=arguments.evaluator_git_sha,
    )
    report = {
        **core,
        "synthesis_report_sha256": sha256_bytes(canonical_bytes(core)),
    }
    output.parent.mkdir(parents=True, exist_ok=False)
    write_json_create_only(output, report)
    print(
        json.dumps(
            {
                "status": "created",
                "synthesis_report_sha256": report["synthesis_report_sha256"],
                "category_claims": list(CATEGORIES),
                "pooled_analysis": "descriptive_only",
            }
        )
    )


def audit_synthesis_report(
    report_path: Path,
    *,
    plan_path: Path,
    evaluation_root: Path,
    protocol_path: Path,
    endpoint_path: Path,
    evaluator_git_sha: str,
) -> dict[str, Any]:
    path = report_path.resolve()
    root = evaluation_root.resolve()
    if path != root / "reports" / "synthesis" / "report.json":
        raise IntegrityError("fixed-v7 synthesis report path changed")
    report = read_json(_existing_regular_file(path, "fixed-v7 synthesis report"))
    _self_hash(report, "synthesis_report_sha256", "fixed-v7 synthesis report")
    expected = _synthesis_report_core(
        plan_path=plan_path.resolve(),
        evaluation_root=root,
        protocol_path=protocol_path.resolve(),
        endpoint_path=endpoint_path.resolve(),
        evaluator_git_sha=evaluator_git_sha,
    )
    if {
        key: value for key, value in report.items() if key != "synthesis_report_sha256"
    } != expected:
        raise IntegrityError("fixed-v7 synthesis report changed")
    return report


def authorize_office_chair(arguments: argparse.Namespace) -> None:
    root = arguments.evaluation_root.resolve()
    protocol = audit_protocol(
        arguments.protocol.resolve(), require_control_results=False
    )
    endpoint = audit_endpoint(arguments.endpoint_receipt.resolve())
    joint = audit_evaluation(
        root,
        arguments.protocol.resolve(),
        arguments.endpoint_receipt.resolve(),
        require_fresh_results=False,
    )
    report_path = root / "reports/laptop/report.json"
    report = read_json(_existing_regular_file(report_path, "fixed-v7 laptop report"))
    _self_hash(report, "report_sha256", "fixed-v7 laptop report")
    authorization = read_json(
        _existing_regular_file(
            root / "reports/laptop/office_chair_authorization.json",
            "fixed-v7 office-chair authorization",
        )
    )
    _self_hash(
        authorization,
        "authorization_sha256",
        "fixed-v7 office-chair authorization",
    )
    office_binding = read_json(root / "bindings/office_chair/binding.json")
    _self_hash(office_binding, "binding_sha256", "fixed-v7 office-chair binding")
    office_launch = read_json(root / "bundles/office_chair/launch_manifest.json")
    audit_launch_manifest(office_launch)
    if (
        authorization.get("schema") != TRANSFER_SCHEMA
        or authorization.get("status") != "authorized"
        or authorization.get("authorization_basis") != "infrastructure_validity_only"
        or authorization.get("score_independent_authorization") is not True
        or authorization.get("candidate_score_used_for_authorization") is not False
        or authorization.get("candidate_selection_or_retuning_after_laptop")
        is not False
        or authorization.get("protocol_sha256") != protocol["protocol_sha256"]
        or authorization.get("evaluation_freeze_sha256")
        != joint["evaluation_freeze_sha256"]
        or authorization.get("laptop_report_sha256") != report["report_sha256"]
        or authorization.get("laptop_report_file_sha256") != sha256_file(report_path)
        or authorization.get("office_chair_binding_sha256")
        != office_binding["binding_sha256"]
        or authorization.get("office_chair_launch_manifest_sha256")
        != office_launch["launch_manifest_sha256"]
        or authorization.get("infrastructure_invalid_runs") != 0
        or authorization.get("bound_runs") != 0
        or office_binding.get("candidate_composite_sha256")
        != endpoint["candidate"]["composite_sha256"]
    ):
        raise IntegrityError(
            "fixed-v7 office-chair score-independent authorization changed"
        )
    print(
        json.dumps(
            {
                "authorized": True,
                "basis": "infrastructure_validity_only",
                "candidate_score_consulted": False,
                "candidate_reconfigured": False,
            }
        )
    )


def audit_synthesis_plan_command(arguments: argparse.Namespace) -> None:
    plan = audit_synthesis_plan(
        arguments.path.resolve(),
        evaluation_root=arguments.evaluation_root.resolve(),
        protocol_path=arguments.protocol.resolve(),
        endpoint_path=arguments.endpoint_receipt.resolve(),
        evaluator_git_sha=arguments.evaluator_git_sha,
    )
    print(
        json.dumps(
            {
                "valid": True,
                "synthesis_plan_sha256": plan["synthesis_plan_sha256"],
                "outcomes_read": False,
            }
        )
    )


def audit_synthesis_report_command(arguments: argparse.Namespace) -> None:
    report = audit_synthesis_report(
        arguments.path.resolve(),
        plan_path=arguments.synthesis_plan.resolve(),
        evaluation_root=arguments.evaluation_root.resolve(),
        protocol_path=arguments.protocol.resolve(),
        endpoint_path=arguments.endpoint_receipt.resolve(),
        evaluator_git_sha=arguments.evaluator_git_sha,
    )
    print(
        json.dumps(
            {
                "valid": True,
                "synthesis_report_sha256": report["synthesis_report_sha256"],
            }
        )
    )


def audit_command(arguments: argparse.Namespace) -> None:
    if arguments.kind == "protocol":
        value = audit_protocol(arguments.path.resolve())
        result = {"valid": True, "protocol_sha256": value["protocol_sha256"]}
    elif arguments.kind == "endpoint":
        value = audit_endpoint(arguments.path.resolve())
        result = {"valid": True, "receipt_sha256": value["receipt_sha256"]}
    else:
        value = audit_evaluation(
            arguments.path.resolve(),
            arguments.protocol.resolve(),
            arguments.endpoint_receipt.resolve(),
        )
        result = {
            "valid": True,
            "evaluation_freeze_sha256": value["evaluation_freeze_sha256"],
        }
    print(json.dumps(result))


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    commands = value.add_subparsers(dest="command", required=True)
    create = commands.add_parser("freeze")
    create.add_argument("--source-protocol", type=Path, required=True)
    create.add_argument("--source-controls", type=Path, required=True)
    create.add_argument("--labels", type=Path, required=True)
    create.add_argument("--endpoint-receipt", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    create.set_defaults(function=freeze)
    prepare = commands.add_parser("render")
    prepare.add_argument("--repository-root", type=Path, required=True)
    prepare.add_argument("--source-preparation", type=Path, required=True)
    prepare.add_argument("--protocol", type=Path, required=True)
    prepare.add_argument("--endpoint-receipt", type=Path, required=True)
    prepare.add_argument("--output-root", type=Path, required=True)
    prepare.add_argument("--laptop-base-port", type=int, required=True)
    prepare.add_argument("--office-chair-base-port", type=int, required=True)
    prepare.add_argument("--python-executable", required=True)
    prepare.set_defaults(function=render)
    final = commands.add_parser("finalize-category")
    final.add_argument("--repository-root", type=Path, required=True)
    final.add_argument("--source-preparation", type=Path, required=True)
    final.add_argument("--protocol", type=Path, required=True)
    final.add_argument("--endpoint-receipt", type=Path, required=True)
    final.add_argument("--evaluation-root", type=Path, required=True)
    final.add_argument("--category", choices=CATEGORIES, required=True)
    final.add_argument("--executor-status", type=Path, required=True)
    final.set_defaults(function=finalize_category)
    authorize = commands.add_parser("authorize-office-chair")
    authorize.add_argument("--protocol", type=Path, required=True)
    authorize.add_argument("--endpoint-receipt", type=Path, required=True)
    authorize.add_argument("--evaluation-root", type=Path, required=True)
    authorize.set_defaults(function=authorize_office_chair)
    preregister = commands.add_parser("preregister-synthesis")
    preregister.add_argument("--protocol", type=Path, required=True)
    preregister.add_argument("--endpoint-receipt", type=Path, required=True)
    preregister.add_argument("--evaluation-root", type=Path, required=True)
    preregister.add_argument("--evaluator-git-sha", required=True)
    preregister.add_argument("--output", type=Path, required=True)
    preregister.set_defaults(function=preregister_synthesis)
    synthesis = commands.add_parser("finalize-synthesis")
    synthesis.add_argument("--protocol", type=Path, required=True)
    synthesis.add_argument("--endpoint-receipt", type=Path, required=True)
    synthesis.add_argument("--evaluation-root", type=Path, required=True)
    synthesis.add_argument("--synthesis-plan", type=Path, required=True)
    synthesis.add_argument("--evaluator-git-sha", required=True)
    synthesis.add_argument("--output", type=Path, required=True)
    synthesis.set_defaults(function=finalize_synthesis)
    audit_plan = commands.add_parser("audit-synthesis-plan")
    audit_plan.add_argument("path", type=Path)
    audit_plan.add_argument("--protocol", type=Path, required=True)
    audit_plan.add_argument("--endpoint-receipt", type=Path, required=True)
    audit_plan.add_argument("--evaluation-root", type=Path, required=True)
    audit_plan.add_argument("--evaluator-git-sha", required=True)
    audit_plan.set_defaults(function=audit_synthesis_plan_command)
    audit_report = commands.add_parser("audit-synthesis-report")
    audit_report.add_argument("path", type=Path)
    audit_report.add_argument("--protocol", type=Path, required=True)
    audit_report.add_argument("--endpoint-receipt", type=Path, required=True)
    audit_report.add_argument("--evaluation-root", type=Path, required=True)
    audit_report.add_argument("--synthesis-plan", type=Path, required=True)
    audit_report.add_argument("--evaluator-git-sha", required=True)
    audit_report.set_defaults(function=audit_synthesis_report_command)
    audit = commands.add_parser("audit")
    audit.add_argument("kind", choices=("protocol", "endpoint", "evaluation"))
    audit.add_argument("path", type=Path)
    audit.add_argument("--protocol", type=Path)
    audit.add_argument("--endpoint-receipt", type=Path)
    audit.set_defaults(function=audit_command)
    return value


def main() -> None:
    arguments = parser().parse_args()
    try:
        arguments.function(arguments)
    except IntegrityError as exc:
        raise SystemExit(f"integrity error: {exc}") from exc


if __name__ == "__main__":
    main()
