#!/usr/bin/env python3
"""Outcome-independent CAVEAT-Shop-five completion for the fixed-v7 candidate.

This protocol reuses every raw and refinement-step-20 observation in the
canonical 320-cell final matrix and schedules exactly one fixed-v7 candidate
run for each of its 160 matched cells.  Freezing and rendering never open a
result, trajectory, observation, report, or score.  In particular, there is
no development-score gate and no outcome-dependent candidate selection.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import tempfile
from collections import Counter, defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from caveat_27b_eval.batch import audit_launch_manifest
from caveat_27b_eval.common import (
    IntegrityError,
    canonical_bytes,
    read_json,
    sha256_bytes,
    sha256_file,
    write_json_create_only,
    write_text_create_only,
)
from caveat_27b_eval.corrected_eval import (
    _causal_spec,
    _cell_hashes,
    _load_source_bundle,
)
from caveat_27b_eval.fixed_v7_candidate_eval import (
    _audit_conversion,
    _convert_atomic,
    _derived_manifest,
    _jsonl,
    _self_hash,
    _summarize,
    _valid_observation,
    audit_endpoint,
)

FREEZE_SCHEMA = "caveat-27b-eval.fixed-v7-caveat_shop-five-freeze.v1"
BUNDLE_SCHEMA = "caveat-27b-eval.fixed-v7-caveat_shop-five-bundle.v1"
BINDING_SCHEMA = "caveat-27b-eval.fixed-v7-caveat_shop-five-binding.v1"
REPORT_SCHEMA = "caveat-27b-eval.fixed-v7-caveat_shop-five-report.v1"
LAUNCH_SCHEMA = "caveat-27b-eval.fixed-v7-caveat_shop-five-launch.v1"

SOURCE_FILES = {
    "preparation.json": "a63046166a64f0a90d42155c0f2f3f12806d60f0f1624e9dff2d945682f4508f",
    "frozen_manifest.json": "773f043c2caaf8926f0e223607312981d9f6075291d134811c67c40a6cd9a35e",
    "endpoint_manifest.json": "df98aa3080620a8d1ea25f5d90c9af43e2b3f0e6b01dc93188d7fbb63fc1d237",
    "model_specs.json": "dc9b56fa7772ddad2ad9893af2e520a460c163dac57dee4a9a30e5fb911c1c34",
    "run_bundle/launch_manifest.json": "10aaa5a8bc906d678a9fc1a6f6c71e479e39f128484f36e6abf7ab9d6919c74b",
}
SOURCE_PREPARATION_BODY_SHA256 = (
    "ee528d2d8c2fa9d31fb8b460451382f1ffc7073c293ca4ccf2ebccb464eef2b1"
)
SOURCE_FROZEN_BODY_SHA256 = (
    "825c3d30b7d92f884b11f4fbf030fc50cfeb706da1b6eadc90b164753a56a909"
)
SOURCE_MATRIX_SHA256 = (
    "8cc2e22af6dc29a296de7bddfbac64d7745f2ba76efcb30a0a061fe4693c3231"
)
SOURCE_LAUNCH_BODY_SHA256 = (
    "e9af46c00c660287ac2eac0012d4a03c0fa5d8c9b9807471fce3958e9bfe3d1b"
)
ENDPOINT_FILE_SHA256 = (
    "b99ddb0e858af7bf9c5cab82e726deb1368fd46505b08651c6247960e8421442"
)
ENDPOINT_BODY_SHA256 = (
    "94c2a9304ae13ebf812faf3b4b75fa5d2de157d925039c247d98d2beaa502ba4"
)
CANDIDATE_COMPOSITE_SHA256 = (
    "1f207c8c65af6f17d4bb13144da3a2270e5df007eba4a0d0d8a39296d135f62e"
)
SCENARIOS = ("backpack", "laptop", "mattress", "office_chair", "tent")
VARIANTS = ("graded", "graded3", "graded4", "mixed")
CONDITIONS = {"clean": 3, "combined": 5}
PAIR_COUNT = 160
CONTROL_RUN_COUNT = 320

ANALYSIS_SPEC = {
    "status": "preregistered_before_caveat_shop_five_candidate_execution",
    "arms": ["raw", "step20", "fixed_v7"],
    "metrics": {
        "primary": "strict_binary",
        "secondary": "preservation_strict",
    },
    "canonical_cells": {
        "scenarios": list(SCENARIOS),
        "variants": list(VARIANTS),
        "conditions": CONDITIONS,
        "pair_count": PAIR_COUNT,
        "control_run_count": CONTROL_RUN_COUNT,
        "candidate_run_count": PAIR_COUNT,
    },
    "comparisons": {
        "primary": "fixed_v7_minus_raw",
        "secondary_descriptive": "fixed_v7_minus_step20",
    },
    "exact_inference": {
        "scope": "separately_by_scenario_and_condition_and_metric",
        "comparison": "fixed_v7_minus_raw",
        "test": "one_sided_exact_sign_test",
        "zero_differences": "excluded",
        "null_positive_probability": 0.5,
        "decision_threshold": None,
        "multiplicity_adjustment": "none_descriptive_confirmatory_matrix",
    },
    "reporting": {
        "scenario_condition_primary": True,
        "variant_condition_descriptive": True,
        "pooled_160_descriptive": True,
        "raw_and_step20_controls_reported": True,
    },
    "decision_policy": {
        "score_gate": None,
        "candidate_selection_from_scores": False,
        "retuning_from_scores": False,
        "retraining_from_scores": False,
        "early_stopping_from_scores": False,
        "launch_all_160_if_infrastructure_valid": True,
    },
}


def _regular(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise IntegrityError(f"{label} is absent or a symlink: {path}")
    return path.resolve()


def _source(
    source_preparation: Path, repository_root: Path
) -> tuple[Any, dict[str, Any]]:
    root = source_preparation.resolve()
    for relative, digest in SOURCE_FILES.items():
        path = _regular(root / relative, f"CAVEAT-Shop-five source {relative}")
        if sha256_file(path) != digest:
            raise IntegrityError(f"CAVEAT-Shop-five source bytes changed: {relative}")
    frozen = read_json(root / "frozen_manifest.json")
    if not isinstance(frozen, dict):
        raise IntegrityError("CAVEAT-Shop-five source frozen manifest is malformed")
    source = _load_source_bundle(
        source_preparation_dir=root,
        repository_root=repository_root.resolve(),
        config=frozen["campaign"],
    )
    if (
        source.preparation.get("preparation_sha256") != SOURCE_PREPARATION_BODY_SHA256
        or source.frozen_manifest.get("manifest_sha256") != SOURCE_FROZEN_BODY_SHA256
        or source.matrix.get("matrix_sha256") != SOURCE_MATRIX_SHA256
        or source.launch_manifest.get("launch_manifest_sha256")
        != SOURCE_LAUNCH_BODY_SHA256
        or len(source.matrix.get("runs", [])) != CONTROL_RUN_COUNT
        or len(source.launches) != CONTROL_RUN_COUNT
    ):
        raise IntegrityError("CAVEAT-Shop-five source identity changed")
    return source, frozen


def _endpoint(path: Path) -> dict[str, Any]:
    resolved = _regular(path, "fixed-v7 CAVEAT-Shop-five endpoint")
    if sha256_file(resolved) != ENDPOINT_FILE_SHA256:
        raise IntegrityError("fixed-v7 CAVEAT-Shop-five endpoint bytes changed")
    value = audit_endpoint(resolved)
    if (
        value.get("receipt_sha256") != ENDPOINT_BODY_SHA256
        or value.get("candidate", {}).get("composite_sha256")
        != CANDIDATE_COMPOSITE_SHA256
    ):
        raise IntegrityError("fixed-v7 CAVEAT-Shop-five endpoint identity changed")
    return value


def _pairs(source: Any) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    grouped: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in source.matrix["runs"]:
        grouped[str(row["pair_id"])][str(row["arm"])] = row
    result: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for pair_id in sorted(grouped):
        arms = grouped[pair_id]
        if set(arms) != {"base", "trained"}:
            raise IntegrityError(f"CAVEAT-Shop-five source pair is incomplete: {pair_id}")
        raw, step20 = arms["base"], arms["trained"]
        fields = (
            "scenario",
            "variant",
            "condition",
            "repetition",
            "block_seed",
            "task_id",
        )
        if any(raw[field] != step20[field] for field in fields):
            raise IntegrityError(f"CAVEAT-Shop-five source pair is unmatched: {pair_id}")
        result.append((raw, step20))
    if len(result) != PAIR_COUNT:
        raise IntegrityError("CAVEAT-Shop-five source does not contain exactly 160 pairs")
    counts = Counter(
        (raw["scenario"], raw["variant"], raw["condition"]) for raw, _ in result
    )
    expected = {
        (scenario, variant, condition): repetitions
        for scenario in SCENARIOS
        for variant in VARIANTS
        for condition, repetitions in CONDITIONS.items()
    }
    if counts != expected:
        raise IntegrityError("CAVEAT-Shop-five source cell balance changed")
    return result


def _candidate_id(row: Mapping[str, Any]) -> tuple[str, str]:
    core = (
        f"fixed_v7_final::{row['scenario']}::{row['variant']}::"
        f"{row['condition']}::r{int(row['repetition']):02d}"
    )
    return f"{core}::fixed_v7", core


def _contracts(source: Any) -> list[dict[str, Any]]:
    contracts: list[dict[str, Any]] = []
    harness = source.frozen_manifest["source_contract"]["harness_sha256"]
    for raw, step20 in _pairs(source):
        raw_launch = source.launches[raw["run_id"]]
        step_launch = source.launches[step20["run_id"]]
        raw_hashes = _cell_hashes(
            source.specs[raw["run_id"]], raw_launch, harness_sha256=harness
        )
        step_hashes = _cell_hashes(
            source.specs[step20["run_id"]], step_launch, harness_sha256=harness
        )
        for field in (
            "task_sha256",
            "harness_sha256",
            "causal_config_sha256",
            "limit_contract_sha256",
        ):
            if raw_hashes[field] != step_hashes[field]:
                raise IntegrityError(
                    f"CAVEAT-Shop-five pair causal contract differs: {raw['pair_id']}"
                )
        candidate_run_id, candidate_pair_id = _candidate_id(raw)
        core = {
            "cell": [
                raw["scenario"],
                raw["variant"],
                raw["condition"],
                raw["repetition"],
            ],
            "source_pair_id": raw["pair_id"],
            "block_seed": raw["block_seed"],
            "raw_run_id": raw["run_id"],
            "raw_config_sha256": raw_launch["config_sha256"],
            "raw_result_path": raw_launch["results"],
            "step20_run_id": step20["run_id"],
            "step20_config_sha256": step_launch["config_sha256"],
            "step20_result_path": step_launch["results"],
            "candidate_run_id": candidate_run_id,
            "candidate_pair_id": candidate_pair_id,
            "task_sha256": raw_hashes["task_sha256"],
            "harness_sha256": raw_hashes["harness_sha256"],
            "causal_config_sha256": raw_hashes["causal_config_sha256"],
            "limit_contract_sha256": raw_hashes["limit_contract_sha256"],
        }
        contracts.append(
            {**core, "cell_contract_sha256": sha256_bytes(canonical_bytes(core))}
        )
    return contracts


def _freeze_core(
    *,
    source_preparation: Path,
    repository_root: Path,
    endpoint_path: Path,
    output_path: Path,
    evaluator_git_sha: str,
    evaluation_root: Path,
) -> dict[str, Any]:
    if re.fullmatch(r"[0-9a-f]{40}", evaluator_git_sha) is None:
        raise IntegrityError("CAVEAT-Shop-five evaluator Git SHA is invalid")
    source, _ = _source(source_preparation, repository_root)
    endpoint = _endpoint(endpoint_path)
    contracts = _contracts(source)
    return {
        "schema": FREEZE_SCHEMA,
        "status": "frozen_before_caveat_shop_five_candidate_execution_or_outcome_access",
        "outcome_blind": True,
        "outcome_content_read_during_freeze": False,
        "development_score_gate": None,
        "evaluator": {
            "git_sha": evaluator_git_sha,
            "module_sha256": sha256_file(Path(__file__).resolve()),
        },
        "source": {
            "preparation_path": str(source_preparation.resolve()),
            "file_sha256": SOURCE_FILES,
            "preparation_body_sha256": SOURCE_PREPARATION_BODY_SHA256,
            "frozen_manifest_body_sha256": SOURCE_FROZEN_BODY_SHA256,
            "matrix_sha256": SOURCE_MATRIX_SHA256,
            "launch_manifest_body_sha256": SOURCE_LAUNCH_BODY_SHA256,
            "raw_role": "exact_zero_control_parent",
            "step20_role": "existing_refinement_step20_control",
            "control_runs": CONTROL_RUN_COUNT,
        },
        "candidate_endpoint": {
            "path": str(endpoint_path.resolve()),
            "file_sha256": ENDPOINT_FILE_SHA256,
            "body_sha256": ENDPOINT_BODY_SHA256,
            "composite_sha256": endpoint["candidate"]["composite_sha256"],
            "model_spec": endpoint["model_spec"],
        },
        "evaluation_root": str(evaluation_root.resolve()),
        "freeze_path": str(output_path.resolve()),
        "cell_contracts": contracts,
        "cell_inventory_sha256": sha256_bytes(canonical_bytes(contracts)),
        "analysis_spec": ANALYSIS_SPEC,
        "scientific_invariants": {
            "same_task_seed_harness_limits_as_controls": True,
            "raw_and_step20_results_reused_without_rerun": True,
            "candidate_runs": PAIR_COUNT,
            "candidate_fixed_before_any_caveat_shop_five_candidate_outcome": True,
            "candidate_selection_or_retuning_from_any_evaluation_score": False,
            "all_cells_launch_without_score_gate": True,
        },
        "planned_artifacts": {
            "bundle": str((evaluation_root / "bundle").resolve()),
            "results": str((evaluation_root / "run_results").resolve()),
            "report": str((evaluation_root / "report/report.json").resolve()),
        },
    }


def freeze(arguments: argparse.Namespace) -> None:
    output = arguments.output.resolve()
    root = arguments.evaluation_root.resolve()
    if output.exists() or output.is_symlink():
        raise IntegrityError(f"refusing to overwrite CAVEAT-Shop-five freeze: {output}")
    if root.exists() or root.is_symlink():
        raise IntegrityError(f"CAVEAT-Shop-five evaluation root is not fresh: {root}")
    core = _freeze_core(
        source_preparation=arguments.source_preparation.resolve(),
        repository_root=arguments.repository_root.resolve(),
        endpoint_path=arguments.endpoint_receipt.resolve(),
        output_path=output,
        evaluator_git_sha=arguments.evaluator_git_sha,
        evaluation_root=root,
    )
    value = {**core, "freeze_sha256": sha256_bytes(canonical_bytes(core))}
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json_create_only(output, value)
    print(
        json.dumps(
            {
                "status": "frozen",
                "freeze_sha256": value["freeze_sha256"],
                "outcomes_read": False,
            }
        )
    )


def audit_freeze(
    path: Path,
    *,
    source_preparation: Path,
    repository_root: Path,
    endpoint_path: Path,
    evaluator_git_sha: str,
    evaluation_root: Path,
) -> dict[str, Any]:
    resolved = _regular(path, "CAVEAT-Shop-five freeze")
    value = read_json(resolved)
    if not isinstance(value, dict) or value.get("schema") != FREEZE_SCHEMA:
        raise IntegrityError("CAVEAT-Shop-five freeze schema changed")
    _self_hash(value, "freeze_sha256", "CAVEAT-Shop-five freeze")
    expected = _freeze_core(
        source_preparation=source_preparation.resolve(),
        repository_root=repository_root.resolve(),
        endpoint_path=endpoint_path.resolve(),
        output_path=resolved,
        evaluator_git_sha=evaluator_git_sha,
        evaluation_root=evaluation_root.resolve(),
    )
    if {key: item for key, item in value.items() if key != "freeze_sha256"} != expected:
        raise IntegrityError("CAVEAT-Shop-five freeze does not reproduce")
    return value


def _overlap(left: Path, right: Path) -> bool:
    try:
        left.relative_to(right)
        return True
    except ValueError:
        pass
    try:
        right.relative_to(left)
        return True
    except ValueError:
        return False


def render(arguments: argparse.Namespace) -> None:
    root = arguments.evaluation_root.resolve()
    freeze_value = audit_freeze(
        arguments.freeze.resolve(),
        source_preparation=arguments.source_preparation.resolve(),
        repository_root=arguments.repository_root.resolve(),
        endpoint_path=arguments.endpoint_receipt.resolve(),
        evaluator_git_sha=arguments.evaluator_git_sha,
        evaluation_root=root,
    )
    if root.exists() or root.is_symlink():
        raise IntegrityError(f"CAVEAT-Shop-five evaluation root is not fresh: {root}")
    if (
        not isinstance(arguments.python_executable, str)
        or not arguments.python_executable
    ):
        raise IntegrityError("CAVEAT-Shop-five Python executable is empty")
    if arguments.base_port < 1024 or arguments.base_port + PAIR_COUNT > 65535:
        raise IntegrityError("CAVEAT-Shop-five storefront port band is invalid")
    source, source_frozen = _source(
        arguments.source_preparation.resolve(), arguments.repository_root.resolve()
    )
    endpoint = _endpoint(arguments.endpoint_receipt.resolve())
    results_root = root / "run_results"
    source_results = Path(source.launch_manifest["results_root"]).resolve()
    if _overlap(results_root, source_results):
        raise IntegrityError("CAVEAT-Shop-five candidate and control result roots overlap")
    derived = _derived_manifest(source_frozen, endpoint)
    contracts = {row["candidate_run_id"]: row for row in freeze_value["cell_contracts"]}
    root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{root.name}.", dir=root.parent
    ) as temporary:
        stage = Path(temporary) / "evaluation"
        bundle = stage / "bundle"
        configs = bundle / "configs"
        configs.mkdir(parents=True)
        launches: list[dict[str, Any]] = []
        mappings: list[dict[str, Any]] = []
        for index, (raw, step20) in enumerate(_pairs(source)):
            candidate_run_id, candidate_pair_id = _candidate_id(raw)
            contract = contracts[candidate_run_id]
            spec = copy.deepcopy(source.specs[raw["run_id"]])
            runtime = copy.deepcopy(spec["runtime_environment"])
            runtime["CAVEAT_CACHE_NONCE"] = (
                f"fixed_v7_caveat_shop_five/{candidate_run_id}"
            )
            audit = copy.deepcopy(spec["audit_contract"])
            audit.update(
                {
                    "endpoint_manifest_sha256": endpoint["receipt_sha256"],
                    "frozen_manifest_sha256": derived["manifest_sha256"],
                    "fixed_v7_caveat_shop_five_freeze_sha256": freeze_value["freeze_sha256"],
                    "fixed_v7_candidate_composite_sha256": CANDIDATE_COMPOSITE_SHA256,
                    "fixed_v7_caveat_shop_five_cell_contract_sha256": contract[
                        "cell_contract_sha256"
                    ],
                    "fixed_v7_caveat_shop_five_no_score_gate": True,
                }
            )
            result = results_root / (
                "caveat_shop__caveat-harness__fixed_v7__"
                f"{raw['scenario']}-{raw['variant']}__{raw['condition']}__"
                f"{candidate_run_id.replace('::', '-')}"
            )
            spec.update(
                {
                    "arm": "fixed_v7",
                    "model": copy.deepcopy(endpoint["model_spec"]),
                    "run_id": candidate_run_id,
                    "pair_id": candidate_pair_id,
                    "port": arguments.base_port + index,
                    "out_dir": str(result),
                    "runtime_environment": runtime,
                    "audit_contract": audit,
                }
            )
            if (
                sha256_bytes(canonical_bytes(_causal_spec(spec)))
                != contract["causal_config_sha256"]
                or sha256_bytes(canonical_bytes(spec["task"]))
                != contract["task_sha256"]
                or audit.get("harness_sha256") != contract["harness_sha256"]
                or audit.get("limit_contract_sha256")
                != contract["limit_contract_sha256"]
                or spec.get("block_seed") != contract["block_seed"]
            ):
                raise IntegrityError(
                    f"CAVEAT-Shop-five candidate causal contract changed: {candidate_run_id}"
                )
            path = configs / f"{index:03d}_{candidate_run_id.replace('::', '-')}.json"
            write_json_create_only(path, spec)
            final_path = root / "bundle" / "configs" / path.name
            launch = {
                "run_id": candidate_run_id,
                "pair_id": candidate_pair_id,
                "arm": "fixed_v7",
                "port": arguments.base_port + index,
                "config": str(final_path),
                "config_sha256": sha256_file(path),
                "results": str(result),
                "argv": [
                    arguments.python_executable,
                    "-m",
                    "caveat_27b_eval.launch_one",
                    "--spec",
                    str(final_path),
                ],
                "environment": runtime,
                "audit_contract": audit,
            }
            launches.append(launch)
            mapping_core = {
                "cell": contract["cell"],
                "cell_contract_sha256": contract["cell_contract_sha256"],
                "raw_run_id": raw["run_id"],
                "raw_config_sha256": source.launches[raw["run_id"]]["config_sha256"],
                "step20_run_id": step20["run_id"],
                "step20_config_sha256": source.launches[step20["run_id"]][
                    "config_sha256"
                ],
                "candidate_run_id": candidate_run_id,
                "candidate_config_sha256": launch["config_sha256"],
            }
            mappings.append(
                {
                    **mapping_core,
                    "mapping_sha256": sha256_bytes(canonical_bytes(mapping_core)),
                }
            )
        launch_core = {
            "schema_version": 2,
            "schema": LAUNCH_SCHEMA,
            "execution_mode": "single_arm_completion",
            "evaluation": "fixed_v7_caveat_shop_five_final",
            "campaign_id": source.launch_manifest["campaign_id"],
            "matrix_sha256": SOURCE_MATRIX_SHA256,
            "endpoint_manifest_sha256": endpoint["receipt_sha256"],
            "protocol_sha256": freeze_value["freeze_sha256"],
            "base_port": arguments.base_port,
            "results_root": str(results_root),
            "launches": launches,
        }
        launch = {
            **launch_core,
            "launch_manifest_sha256": sha256_bytes(canonical_bytes(launch_core)),
        }
        binding_core = {
            "schema": BINDING_SCHEMA,
            "status": "bound_before_caveat_shop_five_candidate_execution",
            "outcomes_read_during_binding": False,
            "freeze_sha256": freeze_value["freeze_sha256"],
            "source_control_launch_manifest_sha256": source.launch_manifest[
                "launch_manifest_sha256"
            ],
            "candidate_launch_manifest_sha256": launch["launch_manifest_sha256"],
            "candidate_composite_sha256": CANDIDATE_COMPOSITE_SHA256,
            "mappings": mappings,
        }
        binding = {
            **binding_core,
            "binding_sha256": sha256_bytes(canonical_bytes(binding_core)),
        }
        bundle_core = {
            "schema": BUNDLE_SCHEMA,
            "status": "rendered_before_caveat_shop_five_candidate_execution",
            "outcomes_read_during_render": False,
            "development_score_gate": None,
            "freeze_sha256": freeze_value["freeze_sha256"],
            "source_control_launch_manifest_sha256": source.launch_manifest[
                "launch_manifest_sha256"
            ],
            "candidate_launch_manifest_sha256": launch["launch_manifest_sha256"],
            "binding_sha256": binding["binding_sha256"],
            "candidate_composite_sha256": CANDIDATE_COMPOSITE_SHA256,
            "control_run_count": CONTROL_RUN_COUNT,
            "candidate_run_count": PAIR_COUNT,
            "storefront_port_band": [
                arguments.base_port,
                arguments.base_port + PAIR_COUNT - 1,
            ],
        }
        receipt = {
            **bundle_core,
            "bundle_sha256": sha256_bytes(canonical_bytes(bundle_core)),
        }
        write_json_create_only(bundle / "launch_manifest.json", launch)
        write_json_create_only(
            bundle / "control_launch_manifest.json", source.launch_manifest
        )
        write_json_create_only(bundle / "frozen_manifest.json", derived)
        write_json_create_only(bundle / "binding.json", binding)
        write_json_create_only(bundle / "bundle.json", receipt)
        write_json_create_only(stage / "freeze.json", freeze_value)
        os.rename(stage, root)
    audit_bundle(
        root,
        freeze_path=arguments.freeze.resolve(),
        source_preparation=arguments.source_preparation.resolve(),
        repository_root=arguments.repository_root.resolve(),
        endpoint_path=arguments.endpoint_receipt.resolve(),
        evaluator_git_sha=arguments.evaluator_git_sha,
        require_fresh_results=True,
    )
    print(
        json.dumps(
            {
                "status": "rendered",
                "candidate_runs": PAIR_COUNT,
                "bundle_sha256": receipt["bundle_sha256"],
                "outcomes_read": False,
            }
        )
    )


def audit_bundle(
    root: Path,
    *,
    freeze_path: Path,
    source_preparation: Path,
    repository_root: Path,
    endpoint_path: Path,
    evaluator_git_sha: str,
    require_fresh_results: bool,
) -> dict[str, Any]:
    resolved = root.resolve()
    freeze_value = audit_freeze(
        freeze_path,
        source_preparation=source_preparation,
        repository_root=repository_root,
        endpoint_path=endpoint_path,
        evaluator_git_sha=evaluator_git_sha,
        evaluation_root=resolved,
    )
    source, _ = _source(source_preparation, repository_root)
    endpoint = _endpoint(endpoint_path)
    bundle = read_json(_regular(resolved / "bundle/bundle.json", "CAVEAT-Shop-five bundle"))
    binding = read_json(
        _regular(resolved / "bundle/binding.json", "CAVEAT-Shop-five binding")
    )
    launch = read_json(
        _regular(resolved / "bundle/launch_manifest.json", "CAVEAT-Shop-five launch")
    )
    controls = read_json(
        _regular(
            resolved / "bundle/control_launch_manifest.json", "CAVEAT-Shop-five controls"
        )
    )
    _self_hash(bundle, "bundle_sha256", "CAVEAT-Shop-five bundle")
    _self_hash(binding, "binding_sha256", "CAVEAT-Shop-five binding")
    audit_launch_manifest(launch)
    audit_launch_manifest(controls)
    contracts = {row["candidate_run_id"]: row for row in freeze_value["cell_contracts"]}
    mappings = {row["candidate_run_id"]: row for row in binding.get("mappings", [])}
    if (
        bundle.get("development_score_gate") is not None
        or bundle.get("outcomes_read_during_render") is not False
        or bundle.get("freeze_sha256") != freeze_value["freeze_sha256"]
        or bundle.get("binding_sha256") != binding["binding_sha256"]
        or bundle.get("candidate_launch_manifest_sha256")
        != launch.get("launch_manifest_sha256")
        or controls != source.launch_manifest
        or len(launch.get("launches", [])) != PAIR_COUNT
        or len(mappings) != PAIR_COUNT
        or set(mappings) != set(contracts)
        or binding.get("candidate_composite_sha256")
        != endpoint["candidate"]["composite_sha256"]
    ):
        raise IntegrityError("CAVEAT-Shop-five bundle inventory or provenance changed")
    for row in launch["launches"]:
        run_id = row["run_id"]
        contract = contracts.get(run_id)
        mapping = mappings.get(run_id)
        if contract is None or mapping is None:
            raise IntegrityError(f"CAVEAT-Shop-five launch is not frozen: {run_id}")
        _self_hash(mapping, "mapping_sha256", "CAVEAT-Shop-five mapping")
        spec = read_json(_regular(Path(row["config"]), "CAVEAT-Shop-five config"))
        audit = spec.get("audit_contract") or {}
        if (
            sha256_file(Path(row["config"])) != row["config_sha256"]
            or spec.get("model") != endpoint["model_spec"]
            or spec.get("run_id") != run_id
            or spec.get("block_seed") != contract["block_seed"]
            or sha256_bytes(canonical_bytes(_causal_spec(spec)))
            != contract["causal_config_sha256"]
            or sha256_bytes(canonical_bytes(spec.get("task")))
            != contract["task_sha256"]
            or audit.get("harness_sha256") != contract["harness_sha256"]
            or audit.get("limit_contract_sha256") != contract["limit_contract_sha256"]
            or audit.get("fixed_v7_caveat_shop_five_no_score_gate") is not True
            or mapping.get("cell_contract_sha256") != contract["cell_contract_sha256"]
        ):
            raise IntegrityError(f"CAVEAT-Shop-five candidate config changed: {run_id}")
        result = Path(row["results"])
        if require_fresh_results and (result.exists() or result.is_symlink()):
            raise IntegrityError(f"CAVEAT-Shop-five candidate result is not fresh: {result}")
    return bundle


def _status(path: Path, launch: Mapping[str, Any]) -> None:
    value = read_json(_regular(path, "CAVEAT-Shop-five executor status"))
    counts = value.get("counts") or {}
    runs = value.get("runs") or {}
    if (
        value.get("launch_manifest_sha256") != launch["launch_manifest_sha256"]
        or value.get("success") is not True
        or counts != {"complete": PAIR_COUNT}
        or not isinstance(runs, dict)
        or set(runs) != {row["run_id"] for row in launch["launches"]}
        or any(
            not isinstance(row, dict)
            or row.get("state") != "complete"
            or row.get("exit_code") != 0
            or row.get("reason") is not None
            for row in runs.values()
        )
    ):
        raise IntegrityError(
            "CAVEAT-Shop-five executor did not complete exactly 160 clean runs"
        )


def _report_core(
    *,
    root: Path,
    freeze_value: Mapping[str, Any],
    bundle: Mapping[str, Any],
    binding: Mapping[str, Any],
    endpoint: Mapping[str, Any],
) -> dict[str, Any]:
    conversion = root / "conversions"
    control_observations = conversion / "controls/observations.jsonl"
    candidate_observations = conversion / "candidate/observations.jsonl"
    control_audit = _audit_conversion(
        conversion / "controls/conversion_audit.json",
        control_observations,
        CONTROL_RUN_COUNT,
    )
    candidate_audit = _audit_conversion(
        conversion / "candidate/conversion_audit.json",
        candidate_observations,
        PAIR_COUNT,
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
                f"CAVEAT-Shop-five observation is absent: {mapping['cell']}"
            )
        _valid_observation(raw, mapping["raw_config_sha256"], "CAVEAT-Shop-five raw")
        _valid_observation(
            step20, mapping["step20_config_sha256"], "CAVEAT-Shop-five step20"
        )
        _valid_observation(
            candidate, mapping["candidate_config_sha256"], "CAVEAT-Shop-five fixed-v7"
        )
        pair: dict[str, Any] = {"cell": mapping["cell"]}
        for name, observation in (
            ("raw", raw),
            ("step20", step20),
            ("fixed_v7", candidate),
        ):
            pair[name] = {
                "strict_binary": float(observation["strict_binary"]),
                "preservation_strict": float(observation["preservation_strict"]),
                "valid_transaction": bool(observation["valid_transaction"]),
                "result": observation.get("result"),
            }
        pairs.append(pair)
    pairs.sort(key=lambda row: tuple(row["cell"]))
    if len(pairs) != PAIR_COUNT:
        raise IntegrityError("CAVEAT-Shop-five report pair count changed")
    scenario_condition = {
        scenario: {
            condition: _summarize(
                [
                    row
                    for row in pairs
                    if row["cell"][0] == scenario and row["cell"][2] == condition
                ]
            )
            for condition in CONDITIONS
        }
        for scenario in SCENARIOS
    }
    variant_condition = {
        variant: {
            condition: _summarize(
                [
                    row
                    for row in pairs
                    if row["cell"][1] == variant and row["cell"][2] == condition
                ]
            )
            for condition in CONDITIONS
        }
        for variant in VARIANTS
    }
    return {
        "schema": REPORT_SCHEMA,
        "status": "complete",
        "freeze_sha256": freeze_value["freeze_sha256"],
        "bundle_sha256": bundle["bundle_sha256"],
        "binding_sha256": binding["binding_sha256"],
        "endpoint_receipt_sha256": endpoint["receipt_sha256"],
        "candidate_composite_sha256": endpoint["candidate"]["composite_sha256"],
        "analysis_spec": ANALYSIS_SPEC,
        "scenario_condition": scenario_condition,
        "variant_condition_descriptive": variant_condition,
        "pooled_160_descriptive": _summarize(pairs),
        "pairs": pairs,
        "conversion_audits": {
            "controls": control_audit["conversion_audit_sha256"],
            "candidate": candidate_audit["conversion_audit_sha256"],
        },
        "invariants": {
            "pair_count": PAIR_COUNT,
            "reused_raw_runs": PAIR_COUNT,
            "reused_step20_runs": PAIR_COUNT,
            "fresh_fixed_v7_runs": PAIR_COUNT,
            "infrastructure_invalid_runs": 0,
            "bound_runs": 0,
            "fresh_scores_recomputed": True,
            "candidate_selection_or_retuning_from_scores": False,
            "development_gate_used": False,
            "identical_task_seed_causal_harness_and_limits": True,
        },
    }


def finalize(arguments: argparse.Namespace) -> None:
    root = arguments.evaluation_root.resolve()
    bundle = audit_bundle(
        root,
        freeze_path=arguments.freeze.resolve(),
        source_preparation=arguments.source_preparation.resolve(),
        repository_root=arguments.repository_root.resolve(),
        endpoint_path=arguments.endpoint_receipt.resolve(),
        evaluator_git_sha=arguments.evaluator_git_sha,
        require_fresh_results=False,
    )
    freeze_value = read_json(arguments.freeze.resolve())
    endpoint = _endpoint(arguments.endpoint_receipt.resolve())
    binding = read_json(root / "bundle/binding.json")
    launch = read_json(root / "bundle/launch_manifest.json")
    controls = read_json(root / "bundle/control_launch_manifest.json")
    candidate_frozen = read_json(root / "bundle/frozen_manifest.json")
    _, control_frozen = _source(
        arguments.source_preparation.resolve(), arguments.repository_root.resolve()
    )
    _status(arguments.executor_status.resolve(), launch)
    report_path = root / "report/report.json"
    if report_path.exists() or (root / "conversions").exists():
        raise IntegrityError("refusing to overwrite CAVEAT-Shop-five conversions/report")
    _convert_atomic(
        launch=controls,
        frozen=control_frozen,
        repository_root=arguments.repository_root.resolve(),
        output=root / "conversions/controls",
    )
    _convert_atomic(
        launch=launch,
        frozen=candidate_frozen,
        repository_root=arguments.repository_root.resolve(),
        output=root / "conversions/candidate",
    )
    core = _report_core(
        root=root,
        freeze_value=freeze_value,
        bundle=bundle,
        binding=binding,
        endpoint=endpoint,
    )
    report = {**core, "report_sha256": sha256_bytes(canonical_bytes(core))}
    report_path.parent.mkdir(parents=True, exist_ok=False)
    write_json_create_only(report_path, report)
    lines = [
        "# Fixed-v7 CAVEAT-Shop-five final completion",
        "",
        "This report is descriptive/confirmatory and has no score-based selection gate.",
        "",
        "| Scenario | Condition | n | Raw | Step20 | Fixed-v7 | Fixed-v7 - raw |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for scenario in SCENARIOS:
        for condition in CONDITIONS:
            summary = core["scenario_condition"][scenario][condition]
            metric = summary["strict_binary"]
            lines.append(
                f"| {scenario} | {condition} | {summary['n']} | {metric['raw_mean']:.1%} | "
                f"{metric['step20_mean']:.1%} | {metric['fixed_v7_mean']:.1%} | "
                f"{metric['fixed_v7_minus_raw']:+.1%} |"
            )
    write_text_create_only(root / "report/report.md", "\n".join(lines) + "\n")
    print(
        json.dumps(
            {
                "status": "complete",
                "report_sha256": report["report_sha256"],
                "pairs": PAIR_COUNT,
                "score_gate": None,
            }
        )
    )


def audit_report(arguments: argparse.Namespace) -> None:
    root = arguments.evaluation_root.resolve()
    bundle = audit_bundle(
        root,
        freeze_path=arguments.freeze.resolve(),
        source_preparation=arguments.source_preparation.resolve(),
        repository_root=arguments.repository_root.resolve(),
        endpoint_path=arguments.endpoint_receipt.resolve(),
        evaluator_git_sha=arguments.evaluator_git_sha,
        require_fresh_results=False,
    )
    freeze_value = read_json(arguments.freeze.resolve())
    endpoint = _endpoint(arguments.endpoint_receipt.resolve())
    binding = read_json(root / "bundle/binding.json")
    report_path = _regular(root / "report/report.json", "CAVEAT-Shop-five report")
    report = read_json(report_path)
    _self_hash(report, "report_sha256", "CAVEAT-Shop-five report")
    expected = _report_core(
        root=root,
        freeze_value=freeze_value,
        bundle=bundle,
        binding=binding,
        endpoint=endpoint,
    )
    if {
        key: item for key, item in report.items() if key != "report_sha256"
    } != expected:
        raise IntegrityError("CAVEAT-Shop-five report does not reproduce")
    print(
        json.dumps(
            {
                "valid": True,
                "report_sha256": report["report_sha256"],
                "pairs": PAIR_COUNT,
            }
        )
    )


def _common(command: argparse.ArgumentParser) -> None:
    command.add_argument("--repository-root", type=Path, required=True)
    command.add_argument("--source-preparation", type=Path, required=True)
    command.add_argument("--endpoint-receipt", type=Path, required=True)
    command.add_argument("--evaluation-root", type=Path, required=True)
    command.add_argument("--evaluator-git-sha", required=True)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    commands = value.add_subparsers(dest="command", required=True)
    freeze_command = commands.add_parser("freeze")
    _common(freeze_command)
    freeze_command.add_argument("--output", type=Path, required=True)
    freeze_command.set_defaults(function=freeze)
    audit_freeze_command = commands.add_parser("audit-freeze")
    _common(audit_freeze_command)
    audit_freeze_command.add_argument("--freeze", type=Path, required=True)
    audit_freeze_command.set_defaults(
        function=lambda args: print(
            json.dumps(
                {
                    "valid": True,
                    "freeze_sha256": audit_freeze(
                        args.freeze.resolve(),
                        source_preparation=args.source_preparation.resolve(),
                        repository_root=args.repository_root.resolve(),
                        endpoint_path=args.endpoint_receipt.resolve(),
                        evaluator_git_sha=args.evaluator_git_sha,
                        evaluation_root=args.evaluation_root.resolve(),
                    )["freeze_sha256"],
                    "outcomes_read": False,
                }
            )
        )
    )
    render_command = commands.add_parser("render")
    _common(render_command)
    render_command.add_argument("--freeze", type=Path, required=True)
    render_command.add_argument("--base-port", type=int, required=True)
    render_command.add_argument("--python-executable", required=True)
    render_command.set_defaults(function=render)
    audit_bundle_command = commands.add_parser("audit-bundle")
    _common(audit_bundle_command)
    audit_bundle_command.add_argument("--freeze", type=Path, required=True)
    audit_bundle_command.set_defaults(
        function=lambda args: print(
            json.dumps(
                {
                    "valid": True,
                    "bundle_sha256": audit_bundle(
                        args.evaluation_root.resolve(),
                        freeze_path=args.freeze.resolve(),
                        source_preparation=args.source_preparation.resolve(),
                        repository_root=args.repository_root.resolve(),
                        endpoint_path=args.endpoint_receipt.resolve(),
                        evaluator_git_sha=args.evaluator_git_sha,
                        require_fresh_results=False,
                    )["bundle_sha256"],
                }
            )
        )
    )
    finalize_command = commands.add_parser("finalize")
    _common(finalize_command)
    finalize_command.add_argument("--freeze", type=Path, required=True)
    finalize_command.add_argument("--executor-status", type=Path, required=True)
    finalize_command.set_defaults(function=finalize)
    audit_report_command = commands.add_parser("audit-report")
    _common(audit_report_command)
    audit_report_command.add_argument("--freeze", type=Path, required=True)
    audit_report_command.set_defaults(function=audit_report)
    return value


def main() -> None:
    arguments = parser().parse_args()
    try:
        arguments.function(arguments)
    except IntegrityError as exc:
        raise SystemExit(f"integrity error: {exc}") from exc


if __name__ == "__main__":
    main()
