#!/usr/bin/env python3
"""Exact eight-cell r4 laptop replay for the Sol-DAgger step-25 candidate.

This evaluator clones the sealed r4 CAVEAT-Harness configs.  The only
model-facing/runtime differences permitted are the candidate endpoint, output
directory, cache nonce, per-run environment port, and provenance attestations.
The task, block seed, scaffold, harness runtime, limits, max steps, and timeout
remain byte-identical after projecting those explicitly allowed fields away.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .batch import audit_launch_manifest
from .common import (
    IntegrityError,
    canonical_bytes,
    read_json,
    sha256_bytes,
    sha256_file,
    write_json_create_only,
    write_text_create_only,
)
from .observations import convert_results
from .sol_dagger_candidate_serve import (
    IMAGE_DIGEST,
    PARENT_TREE,
    TEACHER_EFFORT,
    TEACHER_MODEL,
    validate_endpoint,
    validate_lineage_attestation,
)

PREREG_SCHEMA = "caveat-27b-eval.sol-dagger-laptop-preregistration.v1"
BUNDLE_SCHEMA = "caveat-27b-eval.sol-dagger-laptop-bundle.v1"
LAUNCH_SCHEMA = "caveat-27b-eval.sol-dagger-laptop-launch.v1"
MATRIX_SCHEMA = "caveat-27b-eval.sol-dagger-laptop-matrix.v1"
REPORT_SCHEMA = "caveat-27b-eval.sol-dagger-laptop-report.v1"
INVARIANCE_SCHEMA = "caveat-27b-eval.sol-dagger-r4-config-invariance.v1"
EXACT_LORA_SCHEMA = "caveat-27b-eval.sol-dagger-step25-exact-lora.v1"

# Candidate-specific presentation and lineage values are constants so a later
# receipt-bound update can reuse this exact evaluator implementation.  The
# defaults preserve the sealed step-25 replay byte-for-byte.
CANDIDATE_NAME = "step25-sol-dagger-sft"
CANDIDATE_RESULT_KEY = "sol_dagger_step25"
CANDIDATE_RUN_SLUG = "sol_dagger_step25"
CANDIDATE_DISPLAY_NAME = "Sol-DAgger step25"
CANDIDATE_BINDING_KEY = "sol_dagger_exact_lora_binding"
SCIENTIFIC_LABEL = "same_task_laptop_steered_sol_dagger_development_replay"
SOURCE_STEP = 24
FINAL_STEP = 25
TREATMENT_DESCRIPTION = (
    "The replay differs from sealed r4 only in the receipt-bound Sol-DAgger "
    "step25 direct-LoRA endpoint and non-model-facing output/nonce/port provenance."
)
DIRECTIONAL_TARGET = {
    "strict_successes_at_least": 4,
    "hero_opened_at_least": 6,
    "hero_chosen_at_least": 6,
    "addon_present_at_most": 2,
    "valid_transaction_at_least": 7,
}

R4_LAUNCH_FILE = "ac1efe8b9d0eabedeae6a96d564f6aeb4de957dafd83ba94586e4fbaafba944f"
R4_LAUNCH_BODY = "835158b7f490ca49bb60260c1c2ab983362f52de2e2eb3aee70d8abb30fcd31c"
R4_FROZEN_FILE = "e86d213f65759fd5bbd10640c06d46bfce8a7812acc492bb3d277343c09eaf07"
R4_FROZEN_BODY = "9a150c7eead55fe3acdfcb1166c96fffa59ecb6393f75289ad4af567ff6000e4"
R4_REPORT_FILE = "55ae5ad52a8bd8177725051d0b2261631fc494d1fff544e1fc8dd73d934a7765"
R4_REPORT_BODY = "dbc487d7bcc7f527614c30de33bb8db6b690fc4b626577e5e51161375df8d533"
REPAIR_REPORT_FILE = "e8444cf0b36241c5a523059a004de89f38de9c401342f1968cfcf23d48e336f5"
REPAIR_REPORT_BODY = "7cf72552a50d4b95af49b3222dc591cdb24056943f4c4abe22d17f2e152398ae"

VARIANTS = ("graded", "graded3", "graded4", "mixed")
REPETITIONS = (0, 1)
HERO = "EXP-LAPTOP-50"
ADDON = "ADDON-PLAN"
EXPECTED_CELLS = {
    ("graded", 0): (
        "43301ad0c87d8706c51266fef2da02999cc616415cd09a502e35c8bfa910f964",
        875348673443937884,
        "9e85a8af4b6c7a7e67bc2f5e5713e1b76a0bc6cf1091151149e0ea19b7053a4b",
    ),
    ("graded", 1): (
        "160c8eedb90df0b0f797eac3c2a6298496c0d79dd189692a99a1c680193ae0da",
        8058350307293730227,
        "9e85a8af4b6c7a7e67bc2f5e5713e1b76a0bc6cf1091151149e0ea19b7053a4b",
    ),
    ("graded3", 0): (
        "b5a49b4a5be2514dbb45fee8846f646a879a40aeddcb260e41e39915001ed82a",
        8325525115375234570,
        "7dfeff69510cd885c0c1671d79c4a47244a0c0618dbe4a0c81eb4a4988b46339",
    ),
    ("graded3", 1): (
        "5b646daecce8304990a4d6b2aacea5e0e105372b903e1b788b8cf7de93023a89",
        4281848552838517945,
        "7dfeff69510cd885c0c1671d79c4a47244a0c0618dbe4a0c81eb4a4988b46339",
    ),
    ("graded4", 0): (
        "d6ed014cc43a084e2ed61e742f2357a872e74ca6b9fb93510fb1a6bb852d6a79",
        39201832717686900,
        "b1b3415983a7ef02686bca3abacc75c16f4ffad3c02e2349b6f420b1bf1ca181",
    ),
    ("graded4", 1): (
        "dfcf399b77eb66d29b166597db7f70954041520512a1f4c0bdf8cc9bbc098a38",
        6648480508857059999,
        "b1b3415983a7ef02686bca3abacc75c16f4ffad3c02e2349b6f420b1bf1ca181",
    ),
    ("mixed", 0): (
        "a821ac855fa80e35722f71c38a97ff1d34fa9936eccd4df6bf53c8cca73c11ff",
        7458202074433514561,
        "918f4f4f9fb89a18ba809bf0bf6e651933888c4d694047baedb2afd3cc6bf42b",
    ),
    ("mixed", 1): (
        "297c81eaf13c49e1bc92561d136ccf70042d9419de3290ae5d232fa8b642f4bc",
        8877570344612032238,
        "918f4f4f9fb89a18ba809bf0bf6e651933888c4d694047baedb2afd3cc6bf42b",
    ),
}
ALLOWED_CONFIG_DIFFERENCES = (
    "audit_contract",
    "matrix_sha256",
    "model",
    "out_dir",
    "port",
    "runtime_environment.CAVEAT_CACHE_NONCE",
    "runtime_environment.CAVEAT_EVALUATION_INPUT_ATTESTATION",
)


def _self_hash(value: Mapping[str, Any], field: str, label: str) -> str:
    expected = sha256_bytes(
        canonical_bytes({key: item for key, item in value.items() if key != field})
    )
    if value.get(field) != expected:
        raise IntegrityError(f"{label} has an invalid {field}")
    return expected


def _artifact(path: Path, body_field: str, file_sha: str) -> dict[str, str]:
    path = path.resolve()
    value = read_json(path)
    _self_hash(value, body_field, path.name)
    if sha256_file(path) != file_sha:
        raise IntegrityError(f"frozen source artifact changed: {path}")
    return {
        "path": str(path),
        "file_sha256": file_sha,
        "body_sha256": str(value[body_field]),
    }


def _absolute_without_symlink_dereference(path: Path) -> Path:
    return path.expanduser().absolute()


def _cell(config: Mapping[str, Any]) -> tuple[str, int]:
    variant = ((config.get("task") or {}).get("metadata") or {}).get("variant")
    match = re.search(r"::r(\d\d)::", str(config.get("run_id", "")))
    if variant not in VARIANTS or match is None:
        raise IntegrityError("r4 config is not an expected laptop variant/repetition")
    repetition = int(match.group(1))
    if repetition not in REPETITIONS:
        raise IntegrityError("r4 config repetition changed")
    return str(variant), repetition


def _source_inputs(
    launch_path: Path,
    frozen_path: Path,
    r4_report_path: Path,
    repair_report_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    launch = read_json(launch_path.resolve())
    frozen = read_json(frozen_path.resolve())
    r4_report = read_json(r4_report_path.resolve())
    repair_report = read_json(repair_report_path.resolve())
    audit_launch_manifest(launch)
    _self_hash(frozen, "manifest_sha256", "r4 frozen manifest")
    _self_hash(r4_report, "report_sha256", "r4 report")
    _self_hash(repair_report, "report_sha256", "repair report")
    checks = (
        (launch_path, R4_LAUNCH_FILE, launch.get("launch_manifest_sha256"), R4_LAUNCH_BODY),
        (frozen_path, R4_FROZEN_FILE, frozen.get("manifest_sha256"), R4_FROZEN_BODY),
        (r4_report_path, R4_REPORT_FILE, r4_report.get("report_sha256"), R4_REPORT_BODY),
        (
            repair_report_path,
            REPAIR_REPORT_FILE,
            repair_report.get("report_sha256"),
            REPAIR_REPORT_BODY,
        ),
    )
    for path, expected_file, body, expected_body in checks:
        if sha256_file(path.resolve()) != expected_file or body != expected_body:
            raise IntegrityError(f"sealed evaluation artifact changed: {path}")
    if (
        launch.get("schema") != "caveat-27b-eval.fixed-v7-grpo-fast-launch.v1"
        or launch.get("execution_mode") != "single_arm_completion"
        or launch.get("category") != "laptop"
        or len(launch.get("launches", [])) != 8
        or r4_report.get("status") != "complete"
        or r4_report.get("n") != 8
        or repair_report.get("status") != "complete"
        or repair_report.get("category") != "laptop"
    ):
        raise IntegrityError("sealed r4/repair evaluation semantics changed")
    return launch, frozen, r4_report, repair_report


def _source_rows(launch: Mapping[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any], tuple[str, int]]]:
    rows: list[tuple[dict[str, Any], dict[str, Any], tuple[str, int]]] = []
    for row in launch["launches"]:
        config_path = Path(str(row["config"]))
        config = read_json(config_path)
        cell = _cell(config)
        config_sha, block_seed, task_sha = EXPECTED_CELLS[cell]
        if (
            row.get("config_sha256") != config_sha
            or sha256_file(config_path) != config_sha
            or config.get("scaffold") != "caveat-harness"
            or config.get("condition") != "combined"
            or config.get("max_steps") != 4000
            or config.get("run_timeout_seconds") != 36000
            or config.get("block_seed") != block_seed
            or sha256_bytes(canonical_bytes(config.get("task"))) != task_sha
            or config.get("runtime_environment", {}).get("CAVEAT_CELL_TIMEOUT")
            != "36000"
        ):
            raise IntegrityError(f"sealed r4 cell changed: {cell}")
        rows.append((row, config, cell))
    rows.sort(key=lambda item: (VARIANTS.index(item[2][0]), item[2][1]))
    if [row[2] for row in rows] != [
        (variant, repetition)
        for variant in VARIANTS
        for repetition in REPETITIONS
    ]:
        raise IntegrityError("sealed r4 cell inventory changed")
    return rows


def _harness_projection(config: Mapping[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(dict(config))
    for field in ("audit_contract", "matrix_sha256", "model", "out_dir", "port"):
        value.pop(field, None)
    environment = value.get("runtime_environment")
    if isinstance(environment, dict):
        environment.pop("CAVEAT_CACHE_NONCE", None)
        environment.pop("CAVEAT_EVALUATION_INPUT_ATTESTATION", None)
    return value


def _diff_paths(left: Any, right: Any, prefix: str = "") -> set[str]:
    if isinstance(left, dict) and isinstance(right, dict):
        result: set[str] = set()
        for key in set(left) | set(right):
            child = f"{prefix}.{key}" if prefix else str(key)
            if key not in left or key not in right:
                result.add(child)
            else:
                result.update(_diff_paths(left[key], right[key], child))
        return result
    if left != right:
        return {prefix}
    return set()


def assert_r4_config_invariance(source: Mapping[str, Any], candidate: Mapping[str, Any]) -> str:
    differences = _diff_paths(source, candidate)
    allowed = set(ALLOWED_CONFIG_DIFFERENCES)
    if any(
        not any(path == item or path.startswith(item + ".") for item in allowed)
        for path in differences
    ):
        raise IntegrityError(f"candidate config changed the r4 harness: {sorted(differences)}")
    source_projection = _harness_projection(source)
    candidate_projection = _harness_projection(candidate)
    if source_projection != candidate_projection:
        raise IntegrityError("candidate r4 harness projection is not byte-identical")
    return sha256_bytes(canonical_bytes(source_projection))


def preregister(arguments: argparse.Namespace) -> None:
    launch, _frozen, r4_report, repair_report = _source_inputs(
        arguments.r4_launch,
        arguments.r4_frozen,
        arguments.r4_report,
        arguments.repair_report,
    )
    rows = _source_rows(launch)
    lineage = validate_lineage_attestation(arguments.lineage_attestation)
    endpoint = validate_endpoint(arguments.endpoint_receipt)
    candidate = lineage["candidate"]
    training = lineage["training"]
    collection = lineage["collection"]
    lineage_descriptor = {
        "path": str(arguments.lineage_attestation.resolve()),
        "file_sha256": sha256_file(arguments.lineage_attestation.resolve()),
        "body_sha256": lineage["attestation_sha256"],
    }
    endpoint_descriptor = {
        "path": str(arguments.endpoint_receipt.resolve()),
        "file_sha256": sha256_file(arguments.endpoint_receipt.resolve()),
        "body_sha256": endpoint["receipt_sha256"],
    }
    if (
        endpoint["artifacts"]["pvc_lineage_attestation"] != lineage_descriptor
        or endpoint["candidate"]["adapter_tree_sha256"]
        != candidate["adapter_tree_sha256"]
    ):
        raise IntegrityError("endpoint and supplied PVC lineage attestation differ")
    inventory = [
        {
            "cell": ["laptop", variant, "combined", repetition],
            "source_run_id": row["run_id"],
            "source_config_sha256": row["config_sha256"],
            "source_harness_projection_sha256": sha256_bytes(
                canonical_bytes(_harness_projection(config))
            ),
            "task_sha256": EXPECTED_CELLS[(variant, repetition)][2],
            "block_seed": config["block_seed"],
            "scaffold": config["scaffold"],
            "condition": config["condition"],
            "max_steps": config["max_steps"],
            "run_timeout_seconds": config["run_timeout_seconds"],
        }
        for row, config, (variant, repetition) in rows
    ]
    core = {
        "schema": PREREG_SCHEMA,
        "status": "preregistered_before_candidate_outcomes",
        "outcomes_read_during_preregistration": False,
        "scientific_label": SCIENTIFIC_LABEL,
        "claim_scope": "directional_development_signal_only",
        "evaluation_harness_modified": False,
        "clean_condition_included": False,
        "office_chair_included": False,
        "model_selection_eligible": False,
        "evaluation_root": str(arguments.evaluation_root.resolve()),
        "planned_endpoint_receipt": str(arguments.endpoint_receipt.resolve()),
        "candidate": {
            "name": CANDIDATE_NAME,
            "adapter_tree_sha256": candidate["adapter_tree_sha256"],
            "adapter_config_sha256": candidate["adapter_config_sha256"],
            "trainer_source_git_sha": training["trainer_source_git_sha"],
            "collection_source_git_sha": collection["source_git_sha"],
            "teacher_model": TEACHER_MODEL,
            "teacher_reasoning_effort": TEACHER_EFFORT,
            "parent_tree_sha256": PARENT_TREE,
            "source_step": SOURCE_STEP,
            "final_step": FINAL_STEP,
            "optimizer_updates": 1,
            "fresh_optimizer": True,
            "optimizer_continuation": False,
        },
        "artifacts": {
            "r4_launch": _artifact(arguments.r4_launch, "launch_manifest_sha256", R4_LAUNCH_FILE),
            "r4_frozen": _artifact(arguments.r4_frozen, "manifest_sha256", R4_FROZEN_FILE),
            "r4_report": _artifact(arguments.r4_report, "report_sha256", R4_REPORT_FILE),
            "repair_report": _artifact(
                arguments.repair_report, "report_sha256", REPAIR_REPORT_FILE
            ),
            "pvc_lineage_attestation": lineage_descriptor,
            "endpoint_receipt": endpoint_descriptor,
            **lineage["artifacts"],
        },
        "r4_config_contract": {
            "source": "sealed_r4_candidate_configs",
            "only_allowed_differences": list(ALLOWED_CONFIG_DIFFERENCES),
            "identical_model_facing_harness": True,
            "cell_inventory": inventory,
        },
        "baselines": {
            "repair_step24_combined": repair_report["conditions"]["combined"],
            "prior_r4_metrics": r4_report["metrics"],
            "prior_r4_mechanistic": r4_report["mechanistic"],
        },
        "analysis": {
            "primary_metric": "strict_binary",
            "secondary_metric": "preservation_strict",
            "mechanistic_metrics": [
                "hero_opened",
                "hero_chosen",
                "addon_present_in_final_basket",
                "valid_transaction",
            ],
            "report_all_outcomes": True,
            "directional_target": DIRECTIONAL_TARGET,
            "no_significance_or_generalization_claim": True,
            "compare_to": ["repair_sft_step24", "prior_grpo_r4", "raw", "step20"],
        },
        "planned_launch_manifest": str(
            arguments.evaluation_root.resolve() / "bundle/launch_manifest.json"
        ),
        "planned_report": str(arguments.evaluation_root.resolve() / "report/report.json"),
    }
    value = {**core, "preregistration_sha256": sha256_bytes(canonical_bytes(core))}
    write_json_create_only(arguments.output.resolve(), value)
    print(json.dumps({"status": "preregistered", "sha256": value["preregistration_sha256"]}))


def audit_preregistration(path: Path) -> dict[str, Any]:
    value = read_json(path.resolve())
    _self_hash(value, "preregistration_sha256", "Sol DAgger preregistration")
    inventory = (value.get("r4_config_contract") or {}).get("cell_inventory")
    expected = [
        ["laptop", variant, "combined", repetition]
        for variant in VARIANTS
        for repetition in REPETITIONS
    ]
    candidate = value.get("candidate") or {}
    artifacts = value.get("artifacts") or {}
    inventory_by_cell = {
        tuple(row.get("cell", [])): row for row in inventory or [] if isinstance(row, dict)
    }
    inventory_exact = len(inventory_by_cell) == 8
    for variant in VARIANTS:
        for repetition in REPETITIONS:
            row = inventory_by_cell.get(("laptop", variant, "combined", repetition)) or {}
            config_sha, block_seed, task_sha = EXPECTED_CELLS[(variant, repetition)]
            inventory_exact = inventory_exact and (
                row.get("source_config_sha256") == config_sha
                and row.get("source_harness_projection_sha256")
                and row.get("task_sha256") == task_sha
                and row.get("block_seed") == block_seed
                and row.get("scaffold") == "caveat-harness"
                and row.get("condition") == "combined"
                and row.get("max_steps") == 4000
                and row.get("run_timeout_seconds") == 36000
            )
    if (
        value.get("schema") != PREREG_SCHEMA
        or value.get("status") != "preregistered_before_candidate_outcomes"
        or value.get("outcomes_read_during_preregistration") is not False
        or value.get("scientific_label")
        != SCIENTIFIC_LABEL
        or value.get("claim_scope") != "directional_development_signal_only"
        or value.get("evaluation_harness_modified") is not False
        or value.get("clean_condition_included") is not False
        or value.get("office_chair_included") is not False
        or value.get("model_selection_eligible") is not False
        or not isinstance(inventory, list)
        or [row.get("cell") for row in inventory] != expected
        or not inventory_exact
        or value.get("r4_config_contract", {}).get("only_allowed_differences")
        != list(ALLOWED_CONFIG_DIFFERENCES)
        or value.get("r4_config_contract", {}).get("source")
        != "sealed_r4_candidate_configs"
        or value.get("r4_config_contract", {}).get("identical_model_facing_harness")
        is not True
        or candidate.get("name") != CANDIDATE_NAME
        or candidate.get("parent_tree_sha256") != PARENT_TREE
        or candidate.get("teacher_model") != TEACHER_MODEL
        or candidate.get("teacher_reasoning_effort") != TEACHER_EFFORT
        or candidate.get("source_step") != SOURCE_STEP
        or candidate.get("final_step") != FINAL_STEP
        or candidate.get("optimizer_updates") != 1
        or candidate.get("fresh_optimizer") is not True
        or candidate.get("optimizer_continuation") is not False
        or not isinstance(artifacts, Mapping)
        or set(artifacts)
        != {
            "r4_launch",
            "r4_frozen",
            "r4_report",
            "repair_report",
            "pvc_lineage_attestation",
            "endpoint_receipt",
            "repair_parent_receipt",
            "collection_manifest",
            "training_plan",
            "training_receipt",
        }
        or value.get("analysis", {}).get("directional_target")
        != DIRECTIONAL_TARGET
    ):
        raise IntegrityError("Sol DAgger preregistration policy changed")
    return value


def _assert_preregistered_endpoint(
    prereg: Mapping[str, Any], endpoint: Mapping[str, Any], endpoint_path: Path
) -> None:
    """Bind every later phase to the exact endpoint preregistered before outcomes."""

    resolved = endpoint_path.resolve()
    endpoint_descriptor = {
        "path": str(resolved),
        "file_sha256": sha256_file(resolved),
        "body_sha256": endpoint["receipt_sha256"],
    }
    prereg_artifacts = prereg.get("artifacts") or {}
    endpoint_artifacts = endpoint.get("artifacts") or {}
    prereg_candidate = prereg.get("candidate") or {}
    endpoint_candidate = endpoint.get("candidate") or {}
    endpoint_training = endpoint.get("training") or {}
    if (
        Path(str(prereg.get("planned_endpoint_receipt", ""))).resolve() != resolved
        or prereg_artifacts.get("endpoint_receipt") != endpoint_descriptor
        or prereg_artifacts.get("pvc_lineage_attestation")
        != endpoint_artifacts.get("pvc_lineage_attestation")
        or any(
            prereg_artifacts.get(label) != endpoint_artifacts.get(label)
            for label in (
                "repair_parent_receipt",
                "collection_manifest",
                "training_plan",
                "training_receipt",
            )
        )
        or prereg_candidate.get("adapter_tree_sha256")
        != endpoint_candidate.get("adapter_tree_sha256")
        or prereg_candidate.get("adapter_config_sha256")
        != endpoint_candidate.get("adapter_config_sha256")
        or prereg_candidate.get("trainer_source_git_sha")
        != endpoint_training.get("trainer_source_git_sha")
        or prereg_candidate.get("collection_source_git_sha")
        != endpoint_training.get("collection_source_git_sha")
    ):
        raise IntegrityError("endpoint differs from the exact preregistered endpoint")


def _derived_frozen(source: Mapping[str, Any], endpoint: Mapping[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(dict(source))
    candidate = endpoint["candidate"]
    value["model_contract"]["trained_weight_sha256"] = candidate["composite_sha256"]
    inference = value["inference_contract"]
    inference["container_image_digest"] = IMAGE_DIGEST
    stack = inference["serving_stack"]
    stack["container_image"] = (
        "aifrontiers.azurecr.io/t-yuxuanli/harness-distill@" + IMAGE_DIGEST
    )
    for key in list(stack):
        if key.endswith("exact_lora_binding"):
            stack.pop(key)
    stack[CANDIDATE_BINDING_KEY] = {
        "schema": EXACT_LORA_SCHEMA,
        "endpoint_receipt_sha256": endpoint["receipt_sha256"],
        "pvc_lineage_attestation": endpoint["artifacts"]["pvc_lineage_attestation"],
        "repair_parent_receipt": endpoint["artifacts"]["repair_parent_receipt"],
        "collection_manifest": endpoint["artifacts"]["collection_manifest"],
        "training_receipt": endpoint["artifacts"]["training_receipt"],
        "candidate": candidate,
    }
    value["treatment_difference"] = TREATMENT_DESCRIPTION
    core = {key: item for key, item in value.items() if key != "manifest_sha256"}
    return {**core, "manifest_sha256": sha256_bytes(canonical_bytes(core))}


def render(arguments: argparse.Namespace) -> None:
    prereg = audit_preregistration(arguments.preregistration)
    endpoint = validate_endpoint(arguments.endpoint_receipt)
    source_launch, source_frozen, _r4_report, _repair_report = _source_inputs(
        arguments.r4_launch,
        arguments.r4_frozen,
        arguments.r4_report,
        arguments.repair_report,
    )
    _assert_preregistered_endpoint(prereg, endpoint, arguments.endpoint_receipt)
    if not 1024 <= arguments.base_port <= 65528:
        raise IntegrityError("eight-cell base port is outside the valid range")
    root = arguments.evaluation_root.resolve()
    if str(root) != prereg["evaluation_root"]:
        raise IntegrityError("evaluation root differs from preregistration")
    if root.exists() and any(root.iterdir()):
        allowed = {
            arguments.preregistration.resolve(),
            arguments.endpoint_receipt.resolve(),
            Path(endpoint["artifacts"]["pvc_lineage_attestation"]["path"]).resolve(),
            Path(endpoint["artifacts"]["serve_release"]["path"]).resolve(),
        }
        if any(path.resolve() not in allowed for path in root.rglob("*") if path.is_file()):
            raise IntegrityError("Sol DAgger evaluation root is not fresh")
    bundle_root = root / "bundle"
    results_root = root / "run_results"
    frozen = _derived_frozen(source_frozen, endpoint)
    inference_sha = sha256_bytes(canonical_bytes(frozen["inference_contract"]))
    rows = _source_rows(source_launch)
    cells = [["laptop", variant, "combined", repetition] for _, _, (variant, repetition) in rows]
    matrix_core = {
        "schema": MATRIX_SCHEMA,
        "endpoint_receipt_sha256": endpoint["receipt_sha256"],
        "candidate_composite_sha256": endpoint["candidate"]["composite_sha256"],
        "source_r4_launch_file_sha256": R4_LAUNCH_FILE,
        "source_r4_launch_body_sha256": R4_LAUNCH_BODY,
        "cells": cells,
        "source_config_sha256": [row[0]["config_sha256"] for row in rows],
    }
    matrix_sha = sha256_bytes(canonical_bytes(matrix_core))
    launches: list[dict[str, Any]] = []
    invariance_rows: list[dict[str, Any]] = []
    for index, (source_row, source_config, (variant, repetition)) in enumerate(rows):
        result_path = (
            results_root
            / f"amazon__caveat-harness__{CANDIDATE_RUN_SLUG}__laptop-{variant}__combined__r{repetition:02d}"
        )
        config_path = (
            bundle_root
            / "configs"
            / f"{index:02d}_laptop-{variant}-combined-r{repetition:02d}-sol-dagger.json"
        )
        audit = {
            key: item
            for key, item in source_config["audit_contract"].items()
            if not key.startswith("grpo_fast_")
            and key
            not in {
                "endpoint_manifest_sha256",
                "frozen_manifest_sha256",
                "inference_contract_sha256",
                "matrix_sha256",
            }
        }
        audit.update(
            {
                "endpoint_manifest_sha256": endpoint["receipt_sha256"],
                "frozen_manifest_sha256": frozen["manifest_sha256"],
                "inference_contract_sha256": inference_sha,
                "matrix_sha256": matrix_sha,
                "sol_dagger_preregistration_sha256": prereg["preregistration_sha256"],
                "sol_dagger_endpoint_receipt_sha256": endpoint["receipt_sha256"],
                "sol_dagger_candidate_composite_sha256": endpoint["candidate"]["composite_sha256"],
                "sol_dagger_source_r4_config_sha256": source_row["config_sha256"],
                "sol_dagger_evaluation_harness_modified": False,
            }
        )
        environment = dict(source_config["runtime_environment"])
        environment["CAVEAT_CACHE_NONCE"] = (
            f"{CANDIDATE_RUN_SLUG}/{source_config['run_id']}"
        )
        environment["CAVEAT_EVALUATION_INPUT_ATTESTATION"] = matrix_sha
        config = copy.deepcopy(source_config)
        config.update(
            {
                "port": arguments.base_port + index,
                "out_dir": str(result_path),
                "model": endpoint["model_spec"],
                "matrix_sha256": matrix_sha,
                "runtime_environment": environment,
                "audit_contract": audit,
            }
        )
        projection_sha = assert_r4_config_invariance(source_config, config)
        write_json_create_only(config_path, config)
        config_sha = sha256_file(config_path)
        invariance_rows.append(
            {
                "cell": ["laptop", variant, "combined", repetition],
                "source_config_path": source_row["config"],
                "source_config_sha256": source_row["config_sha256"],
                "candidate_config_path": str(config_path),
                "candidate_config_sha256": config_sha,
                "harness_projection_sha256": projection_sha,
                "only_allowed_differences": list(ALLOWED_CONFIG_DIFFERENCES),
                "identical_model_facing_harness": True,
            }
        )
        launches.append(
            {
                "run_id": config["run_id"],
                "pair_id": config["pair_id"],
                "arm": config["arm"],
                "port": config["port"],
                "results": config["out_dir"],
                "config": str(config_path),
                "config_sha256": config_sha,
                "argv": [
                    str(_absolute_without_symlink_dereference(arguments.python_executable)),
                    "-m",
                    "caveat_27b_eval.launch_one",
                    "--spec",
                    str(config_path),
                ],
                "environment": environment,
                "audit_contract": audit,
            }
        )
    launch_core = {
        "schema": LAUNCH_SCHEMA,
        "schema_version": 1,
        "campaign_id": source_launch["campaign_id"],
        "category": "laptop",
        "evaluation": SCIENTIFIC_LABEL,
        "execution_mode": "single_arm_completion",
        "base_port": arguments.base_port,
        "results_root": str(results_root),
        "matrix_sha256": matrix_sha,
        "endpoint_manifest_sha256": endpoint["receipt_sha256"],
        "protocol_sha256": prereg["preregistration_sha256"],
        "launches": launches,
    }
    launch = {**launch_core, "launch_manifest_sha256": sha256_bytes(canonical_bytes(launch_core))}
    audit_launch_manifest(launch)
    invariance_core = {
        "schema": INVARIANCE_SCHEMA,
        "status": "verified",
        "source_r4_launch_file_sha256": R4_LAUNCH_FILE,
        "source_r4_launch_body_sha256": R4_LAUNCH_BODY,
        "evaluation_harness_modified": False,
        "rows": invariance_rows,
    }
    invariance = {
        **invariance_core,
        "invariance_sha256": sha256_bytes(canonical_bytes(invariance_core)),
    }
    bundle_core = {
        "schema": BUNDLE_SCHEMA,
        "status": "frozen",
        "outcomes_read_during_render": False,
        "evaluation_harness_modified": False,
        "preregistration_sha256": prereg["preregistration_sha256"],
        "endpoint_receipt_sha256": endpoint["receipt_sha256"],
        "candidate_composite_sha256": endpoint["candidate"]["composite_sha256"],
        "frozen_manifest_sha256": frozen["manifest_sha256"],
        "launch_manifest_sha256": launch["launch_manifest_sha256"],
        "matrix_sha256": matrix_sha,
        "invariance_sha256": invariance["invariance_sha256"],
        "cells": cells,
        "candidate_results_present": 0,
    }
    bundle = {**bundle_core, "bundle_sha256": sha256_bytes(canonical_bytes(bundle_core))}
    write_json_create_only(bundle_root / "frozen_manifest.json", frozen)
    write_json_create_only(bundle_root / "launch_manifest.json", launch)
    write_json_create_only(bundle_root / "config_invariance.json", invariance)
    write_json_create_only(bundle_root / "bundle.json", bundle)
    print(
        json.dumps(
            {
                "status": "rendered",
                "runs": 8,
                "launch": launch["launch_manifest_sha256"],
                "bundle": bundle["bundle_sha256"],
                "evaluation_harness_modified": False,
            }
        )
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        value = json.loads(line)
        if not isinstance(value, dict):
            raise IntegrityError("observation row is not an object")
        rows.append(value)
    return rows


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _sign_pvalue(deltas: list[float]) -> float:
    nonzero = [value for value in deltas if value != 0]
    if not nonzero:
        return 1.0
    wins = sum(value > 0 for value in nonzero)
    return sum(
        math.comb(len(nonzero), count) for count in range(wins, len(nonzero) + 1)
    ) / 2 ** len(nonzero)


def _trajectory_diagnostics(path: Path) -> dict[str, Any]:
    value = read_json(path)
    evaluation = value.get("evaluation") or {}
    steps = value.get("steps") or []
    hero_opened = any(
        f"/dp/{HERO}" in str(step.get("url", ""))
        or f"/dp/{HERO}" in str(step.get("action", ""))
        for step in steps
        if isinstance(step, dict)
    )
    basket = (evaluation.get("details") or {}).get("basket") or {}
    line_items = basket.get("line_items") or []
    asins = [str(item.get("asin")) for item in line_items if isinstance(item, dict)]
    return {
        "hero_opened": hero_opened,
        "hero_chosen": evaluation.get("chosen") == HERO,
        "addon_present_in_final_basket": ADDON in asins,
        "final_basket_asins": asins,
        "valid_transaction": evaluation.get("chosen") is not None,
        "outcome": evaluation.get("outcome"),
    }


def finalize(arguments: argparse.Namespace) -> None:
    prereg = audit_preregistration(arguments.preregistration)
    endpoint = validate_endpoint(arguments.endpoint_receipt)
    _assert_preregistered_endpoint(prereg, endpoint, arguments.endpoint_receipt)
    source_launch, source_frozen, r4_report, repair_report = _source_inputs(
        arguments.r4_launch,
        arguments.r4_frozen,
        arguments.r4_report,
        arguments.repair_report,
    )
    source_by_run = {row["run_id"]: config for row, config, _ in _source_rows(source_launch)}
    root = arguments.evaluation_root.resolve()
    launch = read_json(root / "bundle/launch_manifest.json")
    frozen = read_json(root / "bundle/frozen_manifest.json")
    bundle = read_json(root / "bundle/bundle.json")
    invariance = read_json(root / "bundle/config_invariance.json")
    audit_launch_manifest(launch)
    _self_hash(bundle, "bundle_sha256", "Sol DAgger bundle")
    _self_hash(invariance, "invariance_sha256", "Sol DAgger config invariance")
    expected_frozen = _derived_frozen(source_frozen, endpoint)
    if (
        str(root) != prereg.get("evaluation_root")
        or frozen != expected_frozen
        or launch.get("endpoint_manifest_sha256") != endpoint["receipt_sha256"]
        or launch.get("protocol_sha256") != prereg["preregistration_sha256"]
        or bundle.get("preregistration_sha256") != prereg["preregistration_sha256"]
        or bundle.get("endpoint_receipt_sha256") != endpoint["receipt_sha256"]
        or bundle.get("candidate_composite_sha256")
        != endpoint["candidate"]["composite_sha256"]
        or bundle.get("frozen_manifest_sha256") != frozen.get("manifest_sha256")
        or bundle.get("launch_manifest_sha256") != launch.get("launch_manifest_sha256")
        or bundle.get("matrix_sha256") != launch.get("matrix_sha256")
        or bundle.get("invariance_sha256") != invariance.get("invariance_sha256")
        or bundle.get("evaluation_harness_modified") is not False
        or invariance.get("evaluation_harness_modified") is not False
        or len(invariance.get("rows", [])) != 8
    ):
        raise IntegrityError("Sol DAgger bundle no longer proves unchanged evaluation harness")
    for launch_row in launch["launches"]:
        config = read_json(Path(launch_row["config"]))
        source = source_by_run.get(launch_row["run_id"])
        matching = [
            row
            for row in invariance["rows"]
            if row["candidate_config_sha256"] == launch_row["config_sha256"]
        ]
        if (
            source is None
            or len(matching) != 1
            or config.get("model") != endpoint["model_spec"]
            or config.get("matrix_sha256") != launch.get("matrix_sha256")
            or config.get("audit_contract", {}).get("endpoint_manifest_sha256")
            != endpoint["receipt_sha256"]
            or config.get("audit_contract", {}).get(
                "sol_dagger_preregistration_sha256"
            )
            != prereg["preregistration_sha256"]
            or config.get("audit_contract", {}).get(
                "sol_dagger_candidate_composite_sha256"
            )
            != endpoint["candidate"]["composite_sha256"]
            or assert_r4_config_invariance(source, config)
            != matching[0]["harness_projection_sha256"]
        ):
            raise IntegrityError("rendered config invariance proof changed")
    status = read_json(arguments.executor_status.resolve())
    if (
        status.get("launch_manifest_sha256") != launch["launch_manifest_sha256"]
        or status.get("success") is not True
        or status.get("counts") != {"complete": 8}
    ):
        raise IntegrityError("Sol DAgger executor did not complete exactly eight runs")
    conversion = root / "conversion"
    report_root = root / "report"
    if conversion.exists() or report_root.exists():
        raise IntegrityError("refusing to overwrite Sol DAgger outcomes")
    conversion.mkdir(parents=True)
    convert_results(
        launch_manifest=launch,
        frozen_manifest=frozen,
        repository_root=arguments.repository_root.resolve(),
        observations_output=conversion / "observations.jsonl",
        audit_output=conversion / "audit.json",
    )
    conversion_audit = read_json(conversion / "audit.json")
    if (
        conversion_audit.get("complete") is not True
        or conversion_audit.get("expected_runs") != 8
        or conversion_audit.get("observation_rows") != 8
        or conversion_audit.get("infrastructure_invalid_runs") != 0
    ):
        raise IntegrityError("Sol DAgger conversion is incomplete")
    observations = {
        row["run_id"]: row for row in _read_jsonl(conversion / "observations.jsonl")
    }
    repair_pairs = {
        tuple(row["cell"]): row
        for row in repair_report["pairs"]
        if row["cell"][2] == "combined"
    }
    r4_pairs = {tuple(row["cell"]): row for row in r4_report["pairs"]}
    pairs: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for launch_row in launch["launches"]:
        config = read_json(Path(launch_row["config"]))
        variant, repetition = _cell(config)
        cell = ("laptop", variant, "combined", repetition)
        observation = observations.get(launch_row["run_id"])
        repair = repair_pairs.get(cell)
        prior = r4_pairs.get(cell)
        if observation is None or repair is None or prior is None:
            raise IntegrityError(f"missing Sol DAgger comparison pair: {cell}")
        observed_audit = observation.get("audit") or {}
        if (
            observation.get("infrastructure_valid") is not True
            or observed_audit.get("runtime_drift") is not False
            or observed_audit.get("safety_backstop_bound") is not False
            or observed_audit.get("lossy_context_bound") is not False
            or observed_audit.get("recorded_scores_match_fresh") is not True
            or observed_audit.get("launch_config_sha256") != launch_row["config_sha256"]
        ):
            raise IntegrityError(f"invalid Sol DAgger observation: {cell}")
        diagnostic = _trajectory_diagnostics(Path(launch_row["results"]) / "trajectory.json")
        diagnostics.append(diagnostic)
        pairs.append(
            {
                "cell": list(cell),
                "raw": repair["raw"],
                "step20": repair["step20"],
                "repair_sft_step24": repair["fixed_v7"],
                "prior_grpo_r4": prior["grpo_step24"],
                CANDIDATE_RESULT_KEY: {
                    "strict_binary": float(observation["strict_binary"]),
                    "preservation_strict": float(observation["preservation_strict"]),
                    "valid_transaction": bool(observation["valid_transaction"]),
                    "result": observation.get("result"),
                    "diagnostics": diagnostic,
                },
            }
        )
    metric_summary: dict[str, Any] = {}
    arms = ("raw", "step20", "repair_sft_step24", "prior_grpo_r4", CANDIDATE_RESULT_KEY)
    for metric in ("strict_binary", "preservation_strict"):
        values = {
            arm: [float(row[arm][metric]) for row in pairs]
            for arm in arms
        }
        candidate = values[CANDIDATE_RESULT_KEY]
        metric_summary[metric] = {
            **{f"{arm}_mean": _mean(items) for arm, items in values.items()},
            "sol_dagger_minus_repair_sft": _mean(
                [new - old for old, new in zip(values["repair_sft_step24"], candidate, strict=True)]
            ),
            "sol_dagger_minus_prior_grpo_r4": _mean(
                [new - old for old, new in zip(values["prior_grpo_r4"], candidate, strict=True)]
            ),
            "sol_dagger_minus_raw": _mean(
                [new - old for old, new in zip(values["raw"], candidate, strict=True)]
            ),
            "sol_dagger_vs_repair_one_sided_sign_p_descriptive": _sign_pvalue(
                [new - old for old, new in zip(values["repair_sft_step24"], candidate, strict=True)]
            ),
        }
    mechanism = {
        "hero_opened": sum(row["hero_opened"] for row in diagnostics),
        "hero_chosen": sum(row["hero_chosen"] for row in diagnostics),
        "addon_present_in_final_basket": sum(
            row["addon_present_in_final_basket"] for row in diagnostics
        ),
        "valid_transaction": sum(row["valid_transaction"] for row in diagnostics),
        "strict_successes": sum(
            row[CANDIDATE_RESULT_KEY]["strict_binary"] == 1 for row in pairs
        ),
    }
    targets = prereg["analysis"]["directional_target"]
    target_met = {
        "strict_successes": mechanism["strict_successes"] >= targets["strict_successes_at_least"],
        "hero_opened": mechanism["hero_opened"] >= targets["hero_opened_at_least"],
        "hero_chosen": mechanism["hero_chosen"] >= targets["hero_chosen_at_least"],
        "addon_present": mechanism["addon_present_in_final_basket"] <= targets["addon_present_at_most"],
        "valid_transaction": mechanism["valid_transaction"] >= targets["valid_transaction_at_least"],
    }
    core = {
        "schema": REPORT_SCHEMA,
        "status": "complete",
        "scientific_label": SCIENTIFIC_LABEL,
        "claim_scope": "directional_development_signal_only",
        "candidate_selection_eligible": False,
        "no_significance_or_generalization_claim": True,
        "evaluation_harness_modified": False,
        "preregistration_sha256": prereg["preregistration_sha256"],
        "endpoint_receipt_sha256": endpoint["receipt_sha256"],
        "candidate_composite_sha256": endpoint["candidate"]["composite_sha256"],
        "launch_manifest_sha256": launch["launch_manifest_sha256"],
        "config_invariance_sha256": invariance["invariance_sha256"],
        "conversion_audit_sha256": conversion_audit["conversion_audit_sha256"],
        "n": 8,
        "condition": "combined",
        "metrics": metric_summary,
        "mechanistic": mechanism,
        "directional_targets": {
            "definition": targets,
            "met": target_met,
            "all_met": all(target_met.values()),
        },
        "pairs": pairs,
        "invariants": {
            "candidate_runs": 8,
            "clean_runs": 0,
            "infrastructure_invalid_runs": 0,
            "fresh_scores_recomputed": True,
            "sealed_r4_config_harness_projection_byte_identical": True,
            "identical_task_seed_scaffold_runtime_limits_max_steps_timeout": True,
            "only_endpoint_output_nonce_ports_and_provenance_differ": True,
            "office_chair_outcomes_or_training_data_used": False,
            "report_created_regardless_of_score": True,
        },
    }
    report = {**core, "report_sha256": sha256_bytes(canonical_bytes(core))}
    report_root.mkdir(parents=True)
    write_json_create_only(report_root / "report.json", report)
    strict = metric_summary["strict_binary"]
    lines = [
        f"# {CANDIDATE_DISPLAY_NAME} laptop replay",
        "",
        "Eight steered laptop development cells cloned from sealed r4; no clean or held-out claim.",
        "",
        f"| Metric | {CANDIDATE_DISPLAY_NAME} | Repair SFT step24 | Prior GRPO r4 |",
        "|---|---:|---:|---:|",
        f"| Strict success | {strict[f'{CANDIDATE_RESULT_KEY}_mean']:.1%} | {strict['repair_sft_step24_mean']:.1%} | {strict['prior_grpo_r4_mean']:.1%} |",
        f"| Hero opened | {mechanism['hero_opened']}/8 | 1/8 | — |",
        f"| Hero chosen | {mechanism['hero_chosen']}/8 | — | — |",
        f"| Add-on retained | {mechanism['addon_present_in_final_basket']}/8 | 7/8 | — |",
        f"| Valid transaction | {mechanism['valid_transaction']}/8 | — | — |",
        "",
        f"All preregistered directional targets met: `{all(target_met.values())}`.",
        "",
        "Evaluation harness modified: `false`.",
    ]
    write_text_create_only(report_root / "report.md", "\n".join(lines) + "\n")
    print(
        json.dumps(
            {
                "status": "complete",
                "report_sha256": report["report_sha256"],
                "strict": strict[f"{CANDIDATE_RESULT_KEY}_mean"],
                "targets_met": all(target_met.values()),
                "evaluation_harness_modified": False,
            }
        )
    )


def audit_command(arguments: argparse.Namespace) -> None:
    value = read_json(arguments.path.resolve())
    if arguments.kind == "preregistration":
        value = audit_preregistration(arguments.path)
        digest = value["preregistration_sha256"]
    else:
        _self_hash(value, "report_sha256", "Sol DAgger report")
        if (
            value.get("schema") != REPORT_SCHEMA
            or value.get("status") != "complete"
            or value.get("n") != 8
            or value.get("evaluation_harness_modified") is not False
        ):
            raise IntegrityError("Sol DAgger report policy changed")
        digest = value["report_sha256"]
    print(json.dumps({"valid": True, "sha256": digest}))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    commands = root.add_subparsers(dest="command", required=True)

    prereg = commands.add_parser("preregister")
    prereg.add_argument("--r4-launch", type=Path, required=True)
    prereg.add_argument("--r4-frozen", type=Path, required=True)
    prereg.add_argument("--r4-report", type=Path, required=True)
    prereg.add_argument("--repair-report", type=Path, required=True)
    prereg.add_argument("--lineage-attestation", type=Path, required=True)
    prereg.add_argument("--evaluation-root", type=Path, required=True)
    prereg.add_argument("--endpoint-receipt", type=Path, required=True)
    prereg.add_argument("--output", type=Path, required=True)

    render_parser = commands.add_parser("render")
    render_parser.add_argument("--preregistration", type=Path, required=True)
    render_parser.add_argument("--endpoint-receipt", type=Path, required=True)
    render_parser.add_argument("--r4-launch", type=Path, required=True)
    render_parser.add_argument("--r4-frozen", type=Path, required=True)
    render_parser.add_argument("--r4-report", type=Path, required=True)
    render_parser.add_argument("--repair-report", type=Path, required=True)
    render_parser.add_argument("--evaluation-root", type=Path, required=True)
    render_parser.add_argument("--base-port", type=int, required=True)
    render_parser.add_argument("--python-executable", type=Path, required=True)

    finish = commands.add_parser("finalize")
    finish.add_argument("--preregistration", type=Path, required=True)
    finish.add_argument("--endpoint-receipt", type=Path, required=True)
    finish.add_argument("--r4-launch", type=Path, required=True)
    finish.add_argument("--r4-frozen", type=Path, required=True)
    finish.add_argument("--r4-report", type=Path, required=True)
    finish.add_argument("--repair-report", type=Path, required=True)
    finish.add_argument("--evaluation-root", type=Path, required=True)
    finish.add_argument("--executor-status", type=Path, required=True)
    finish.add_argument("--repository-root", type=Path, required=True)

    audit = commands.add_parser("audit")
    audit.add_argument("kind", choices=("preregistration", "report"))
    audit.add_argument("path", type=Path)
    return root


def main() -> None:
    arguments = parser().parse_args()
    functions = {
        "preregister": preregister,
        "render": render,
        "finalize": finalize,
        "audit": audit_command,
    }
    try:
        functions[arguments.command](arguments)
    except IntegrityError as exc:
        raise SystemExit(f"SolDaggerLaptopEvalError: {exc}") from exc


if __name__ == "__main__":
    main()
