"""Four-cell closed-loop holdout macro gate for CAVEAT-27B campaign 2.

The renderer clones only the exact h12 holdout configurations and block seeds
from the frozen interactive collector bundle.  It replaces routing with the
receipt-bound candidate endpoint and reuses ``launch_one`` unchanged.  No
collector result, trajectory, trace, reward, or evaluator output is read while
rendering.  A separate terminal finalizer checks the complete purchase macro;
the artifacts are development gates and never inputs to sealed-r4 evaluation.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .batch import audit_launch_manifest
from .common import (
    IntegrityError,
    canonical_bytes,
    classify_marketplace_result,
    read_json,
    sha256_bytes,
    sha256_file,
    write_json_create_only,
)
from .interactive_sol_dagger_candidate_serve import LOCAL_BASE_URL, validate_endpoint
from .interactive_sol_dagger_heldout_probe import validate_heldout_manifest

COLLECTOR_SCHEMA = "harness-distill.interactive-sol-dagger-rollout-bundle.v1"
LAUNCH_SCHEMA = "caveat-27b-eval.interactive-sol-dagger-heldout-macro-launch.v1"
PREREG_SCHEMA = "caveat-27b-eval.interactive-sol-dagger-heldout-macro-prereg.v1"
REPORT_SCHEMA = "caveat-27b-eval.interactive-sol-dagger-heldout-macro-report.v1"
CAMPAIGN = "caveat-27b-step25-interactive-sol-dagger-r1"
RUN_SLUG = "interactive-sol-dagger-c2-heldout-macro"
COLLECTOR_GIT_SHA = "3a018046f11b3b0b86fec2b0029466d0b5f9fad2"
COLLECTOR_BUNDLE_FILE_SHA256 = (
    "d76abef7988a5c0ace37343ae48d22eee58a840b5a4fb6c55dfdc44ca9c1ac8a"
)
COLLECTOR_BUNDLE_BODY_SHA256 = (
    "a1306e3d1b38b2fbb4c854f583bfe8feb31d55be99428a68d4b0dd8762f8ab70"
)
HARNESS_SHA256 = "d14915ced60a940a27e5d36823acac57762c9754aee71d23b97c40eb506b7da4"
LIMIT_SHA256 = "c0d3219add3dec27fd38d1d504370f52f7051260691259859f60429de0747e2a"
HERO = "EXP-LAPTOP-50"
VARIANTS = ("graded", "graded3", "graded4", "mixed")
HOLDOUT_CELLS = {
    "graded": {
        "index": 8,
        "block_seed": 6957721093490391687,
        "config_sha256": "4bedef99281f47c79a35ffe5e4ffe16972432ffbc8ce39cd85fe8485b6e976b8",
        "source_config_sha256": "43301ad0c87d8706c51266fef2da02999cc616415cd09a502e35c8bfa910f964",
    },
    "graded3": {
        "index": 17,
        "block_seed": 1375347607327541710,
        "config_sha256": "6c78203ff43210fdbb2a5fe4ed00e44cd43ef2602126ff3d99c2d9a89a3527be",
        "source_config_sha256": "b5a49b4a5be2514dbb45fee8846f646a879a40aeddcb260e41e39915001ed82a",
    },
    "graded4": {
        "index": 26,
        "block_seed": 3172142064549898987,
        "config_sha256": "3f7f52cdeea476e8cdd88912291d6587bcf771f71c9f9d59c745c65edf4388d5",
        "source_config_sha256": "d6ed014cc43a084e2ed61e742f2357a872e74ca6b9fb93510fb1a6bb852d6a79",
    },
    "mixed": {
        "index": 35,
        "block_seed": 4101859723391570942,
        "config_sha256": "dfbf2966b84fe74580b760007683f2db61fcad7ae56362351fa4aec8980c4d7b",
        "source_config_sha256": "a821ac855fa80e35722f71c38a97ff1d34fa9936eccd4df6bf53c8cca73c11ff",
    },
}
GATE_DEFINITION = {
    "runs": 4,
    "exact_hero50_orders_required": 4,
    "buy_now_actions_at_most": 0,
    "ordered_cart_delete_checkout_place_order_macro_required_for_every_run": True,
}
_BUY_NOW = re.compile(r"ax_name=(?:'|\")Buy Now(?:'|\")", re.IGNORECASE)
_CART = re.compile(r"ax_name=(?:'|\")Cart(?:'|\")", re.IGNORECASE)
_DELETE = re.compile(r"ax_name=(?:'|\")Delete(?:'|\")", re.IGNORECASE)
_CHECKOUT = re.compile(
    r"ax_name=(?:'|\")Proceed to checkout(?:'|\")", re.IGNORECASE
)
_ORDER = re.compile(r"ax_name=(?:'|\")Place your order(?:'|\")", re.IGNORECASE)


def _self_hash(value: Mapping[str, Any], field: str, label: str) -> str:
    expected = sha256_bytes(
        canonical_bytes({key: item for key, item in value.items() if key != field})
    )
    if value.get(field) != expected:
        raise IntegrityError(f"{label} has an invalid {field}")
    return expected


def _variant(config: Mapping[str, Any]) -> str:
    value = ((config.get("task") or {}).get("metadata") or {}).get("variant")
    if value not in VARIANTS:
        raise IntegrityError("macro source config has an unexpected variant")
    return str(value)


def _model_facing_contract(config: Mapping[str, Any]) -> dict[str, Any]:
    runtime = dict(config.get("runtime_environment") or {})
    runtime.pop("CAVEAT_CACHE_NONCE", None)
    runtime.pop("CAVEAT_EVALUATION_INPUT_ATTESTATION", None)
    model = dict(config.get("model") or {})
    for field in ("base_url", "deployment", "name"):
        model.pop(field, None)
    return {
        "campaign_id": config.get("campaign_id"),
        "condition": config.get("condition"),
        "env": config.get("env"),
        "headless": config.get("headless"),
        "max_steps": config.get("max_steps"),
        "run_timeout_seconds": config.get("run_timeout_seconds"),
        "scaffold": config.get("scaffold"),
        "task": config.get("task"),
        "model_nonrouting": model,
        "runtime_environment_nonidentity": runtime,
    }


def validate_collector_bundle(path: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    path = path.resolve()
    bundle = read_json(path)
    if not isinstance(bundle, dict) or bundle.get("schema") != COLLECTOR_SCHEMA:
        raise IntegrityError("collector bundle schema changed")
    _self_hash(bundle, "bundle_sha256", "collector bundle")
    if (
        sha256_file(path) != COLLECTOR_BUNDLE_FILE_SHA256
        or bundle.get("bundle_sha256") != COLLECTOR_BUNDLE_BODY_SHA256
        or bundle.get("status") != "preregistered"
        or bundle.get("campaign") != CAMPAIGN
        or bundle.get("matrix")
        != {
            "condition": "combined",
            "variants": list(VARIANTS),
            "train_horizons": [2, 8, 16, 24],
            "train_replicas": [0, 1],
            "holdout_horizon": 12,
            "train_runs": 32,
            "holdout_runs": 4,
            "runs": 36,
        }
        or (bundle.get("invariance") or {}).get("model_facing_harness_unchanged")
        is not True
        or (bundle.get("invariance") or {}).get("harness_sha256") != HARNESS_SHA256
        or (bundle.get("invariance") or {}).get("limit_contract_sha256") != LIMIT_SHA256
        or (bundle.get("invariance") or {}).get("evaluation_rows") != 0
        or (bundle.get("invariance") or {}).get("teacher_or_outcome_used_for_render")
        is not False
        or len(bundle.get("rows") or []) != 36
    ):
        raise IntegrityError("collector bundle identity or policy changed")
    holdout: dict[str, dict[str, Any]] = {}
    for row in bundle["rows"]:
        if not isinstance(row, dict) or row.get("split") != "holdout":
            continue
        variant = row.get("variant")
        expected = HOLDOUT_CELLS.get(str(variant))
        config_path = Path(str(row.get("config", ""))).resolve()
        source_path = Path(str(row.get("source_config", ""))).resolve()
        if (
            expected is None
            or variant in holdout
            or row.get("index") != expected["index"]
            or row.get("horizon") != 12
            or row.get("replica") != 0
            or row.get("block_seed") != expected["block_seed"]
            or row.get("config_sha256") != expected["config_sha256"]
            or row.get("source_config_sha256") != expected["source_config_sha256"]
            or sha256_file(config_path) != expected["config_sha256"]
            or sha256_file(source_path) != expected["source_config_sha256"]
        ):
            raise IntegrityError("collector h12 holdout cell identity changed")
        config = read_json(config_path)
        source = read_json(source_path)
        audit = config.get("audit_contract") or {}
        if (
            _variant(config) != variant
            or config.get("block_seed") != expected["block_seed"]
            or source.get("block_seed") == config.get("block_seed")
            or config.get("condition") != "combined"
            or config.get("scaffold") != "caveat-harness"
            or config.get("max_steps") != 4000
            or config.get("run_timeout_seconds") != 36000
            or audit.get("campaign") != CAMPAIGN
            or audit.get("collection_only") is not True
            or audit.get("evaluation_row") is not False
            or audit.get("teacher_or_outcome_used_for_render") is not False
            or audit.get("harness_sha256") != HARNESS_SHA256
            or audit.get("limit_contract_sha256") != LIMIT_SHA256
            or _model_facing_contract(config) != _model_facing_contract(source)
        ):
            raise IntegrityError("collector h12 config harness or seed changed")
        holdout[str(variant)] = {**row, "config_value": config, "source_value": source}
    if tuple(holdout) != VARIANTS:
        raise IntegrityError("collector bundle lacks the exact four h12 holdout cells")
    return bundle, holdout


def _artifact(path: Path, body_field: str) -> dict[str, str]:
    value = read_json(path.resolve())
    _self_hash(value, body_field, path.name)
    return {
        "path": str(path.resolve()),
        "file_sha256": sha256_file(path.resolve()),
        "body_sha256": str(value[body_field]),
    }


def render(arguments: argparse.Namespace) -> None:
    endpoint_path = arguments.endpoint_receipt.resolve()
    heldout_path = arguments.heldout_manifest.resolve()
    collector_path = arguments.collector_bundle.resolve()
    endpoint = validate_endpoint(endpoint_path)
    heldout, _static_rows, _audits = validate_heldout_manifest(
        heldout_path,
        expected_file_sha256=arguments.expected_heldout_file_sha256,
        expected_body_sha256=arguments.expected_heldout_body_sha256,
    )
    collector, source_rows = validate_collector_bundle(collector_path)
    if (
        sha256_file(endpoint_path) != arguments.expected_endpoint_file_sha256
        or endpoint.get("receipt_sha256") != arguments.expected_endpoint_body_sha256
    ):
        raise IntegrityError("macro endpoint identity changed")
    root = arguments.output_root.resolve()
    if root.exists() or root.is_symlink():
        raise IntegrityError("macro output root must be fresh")
    if not 1024 <= arguments.base_port <= 65532:
        raise IntegrityError("macro four-port range is invalid")
    python = arguments.python_executable.expanduser().absolute()
    if not python.is_file():
        raise IntegrityError("macro Python executable is absent")
    root.mkdir(parents=True)
    (root / "configs").mkdir()
    (root / "run_results").mkdir()
    candidate = endpoint["candidate"]
    source_cells = [
        {
            "variant": variant,
            "horizon": 12,
            "replica": 0,
            "block_seed": HOLDOUT_CELLS[variant]["block_seed"],
            "collector_config_sha256": HOLDOUT_CELLS[variant]["config_sha256"],
            "collector_index": HOLDOUT_CELLS[variant]["index"],
        }
        for variant in VARIANTS
    ]
    matrix_core = {
        "development_gate": "closed_loop_h12_holdout_macro",
        "candidate_tree_sha256": candidate["adapter_tree_sha256"],
        "endpoint_receipt_sha256": endpoint["receipt_sha256"],
        "heldout_manifest_sha256": heldout["manifest_sha256"],
        "collector_bundle_sha256": collector["bundle_sha256"],
        "collector_git_sha": COLLECTOR_GIT_SHA,
        "cells": source_cells,
    }
    matrix_sha = sha256_bytes(canonical_bytes(matrix_core))
    launches: list[dict[str, Any]] = []
    projections: list[dict[str, Any]] = []
    for offset, variant in enumerate(VARIANTS):
        source_row = source_rows[variant]
        source = source_row["config_value"]
        config = copy.deepcopy(source)
        run_id = f"{RUN_SLUG}::{variant}::h12::{endpoint['receipt_sha256'][:12]}"
        result_path = root / "run_results" / f"{variant}-h12"
        config_path = root / "configs" / f"{offset:02d}_{variant}_h12.json"
        environment = dict(source["runtime_environment"])
        environment["CAVEAT_CACHE_NONCE"] = run_id
        environment["CAVEAT_EVALUATION_INPUT_ATTESTATION"] = matrix_sha
        audit = {
            "matrix_sha256": matrix_sha,
            "endpoint_manifest_sha256": endpoint["receipt_sha256"],
            "heldout_manifest_sha256": heldout["manifest_sha256"],
            "collector_bundle_sha256": collector["bundle_sha256"],
            "collector_git_sha": COLLECTOR_GIT_SHA,
            "collector_config_sha256": source_row["config_sha256"],
            "collector_h12_block_seed": source_row["block_seed"],
            "development_gate_only": True,
            "part_of_sealed_final_evaluation": False,
            "candidate_only_from_first_decision": True,
            "interactive_teacher_proxy_used": False,
            "training_row": False,
            "sealed_r4_seed_used": False,
            "harness_sha256": HARNESS_SHA256,
            "limit_contract_sha256": LIMIT_SHA256,
        }
        config.update(
            {
                "arm": "candidate",
                "model": endpoint["model_spec"],
                "out_dir": str(result_path),
                "pair_id": run_id,
                "port": arguments.base_port + offset,
                "run_id": run_id,
                "runtime_environment": environment,
                "audit_contract": audit,
            }
        )
        if (
            config.get("block_seed") != HOLDOUT_CELLS[variant]["block_seed"]
            or _model_facing_contract(config) != _model_facing_contract(source)
            or config["model"]["base_url"] != LOCAL_BASE_URL
        ):
            raise IntegrityError("macro render changed h12 seed or model-facing harness")
        write_json_create_only(config_path, config)
        config_sha = sha256_file(config_path)
        launches.append(
            {
                "run_id": run_id,
                "pair_id": run_id,
                "arm": "candidate",
                "port": arguments.base_port + offset,
                "results": str(result_path),
                "config": str(config_path),
                "config_sha256": config_sha,
                "argv": [
                    str(python),
                    "-m",
                    "caveat_27b_eval.launch_one",
                    "--spec",
                    str(config_path),
                ],
                "environment": environment,
                "audit_contract": audit,
            }
        )
        projections.append(
            {
                "variant": variant,
                "source_config": source_row["config"],
                "source_config_sha256": source_row["config_sha256"],
                "candidate_config": str(config_path),
                "candidate_config_sha256": config_sha,
                "block_seed": config["block_seed"],
                "model_facing_projection_sha256": sha256_bytes(
                    canonical_bytes(_model_facing_contract(config))
                ),
                "model_facing_harness_unchanged": True,
            }
        )
    launch_core = {
        "schema": LAUNCH_SCHEMA,
        "schema_version": 1,
        "campaign_id": RUN_SLUG,
        "category": "laptop",
        "evaluation": "c2_h12_holdout_closed_loop_macro_development_gate",
        "execution_mode": "single_arm_completion",
        "base_port": arguments.base_port,
        "results_root": str(root / "run_results"),
        "matrix_sha256": matrix_sha,
        "endpoint_manifest_sha256": endpoint["receipt_sha256"],
        "launches": launches,
    }
    launch = {
        **launch_core,
        "launch_manifest_sha256": sha256_bytes(canonical_bytes(launch_core)),
    }
    audit_launch_manifest(launch)
    write_json_create_only(root / "launch_manifest.json", launch)
    prereg_core = {
        "schema": PREREG_SCHEMA,
        "status": "rendered_before_candidate_outcomes",
        "outcomes_read_during_render": False,
        "collector_results_read_during_render": False,
        "collector_traces_read_during_render": False,
        "development_gate_only": True,
        "part_of_sealed_final_evaluation": False,
        "candidate_only_from_first_decision": True,
        "interactive_teacher_proxy_used": False,
        "training_rows_read": 0,
        "heldout_teacher_manifest_validated": True,
        "heldout_teacher_rows_read_for_validation": 24,
        "heldout_teacher_targets_used_for_training": False,
        "heldout_teacher_targets_used_to_modify_harness": False,
        "cells": 4,
        "matrix_sha256": matrix_sha,
        "gate_definition": GATE_DEFINITION,
        "endpoint": _artifact(endpoint_path, "receipt_sha256"),
        "heldout": _artifact(heldout_path, "manifest_sha256"),
        "collector": {
            "path": str(collector_path),
            "file_sha256": COLLECTOR_BUNDLE_FILE_SHA256,
            "body_sha256": COLLECTOR_BUNDLE_BODY_SHA256,
            "git_sha": COLLECTOR_GIT_SHA,
        },
        "launch": _artifact(root / "launch_manifest.json", "launch_manifest_sha256"),
        "source_cells": source_cells,
        "projections": projections,
    }
    prereg = {
        **prereg_core,
        "preregistration_sha256": sha256_bytes(canonical_bytes(prereg_core)),
    }
    write_json_create_only(root / "preregistration.json", prereg)
    print(
        json.dumps(
            {
                "status": "rendered",
                "runs": 4,
                "launch_manifest_sha256": launch["launch_manifest_sha256"],
                "preregistration_sha256": prereg["preregistration_sha256"],
                "candidate_only_from_first_decision": True,
            },
            sort_keys=True,
        )
    )


def audit_launch(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    path = path.resolve()
    root = path.parent
    launch = read_json(path)
    prereg_path = root / "preregistration.json"
    prereg = read_json(prereg_path)
    audit_launch_manifest(launch)
    _self_hash(prereg, "preregistration_sha256", "macro preregistration")
    endpoint_record = prereg.get("endpoint") or {}
    heldout_record = prereg.get("heldout") or {}
    collector_record = prereg.get("collector") or {}
    endpoint_path = Path(str(endpoint_record.get("path", ""))).resolve()
    heldout_path = Path(str(heldout_record.get("path", ""))).resolve()
    collector_path = Path(str(collector_record.get("path", ""))).resolve()
    endpoint = validate_endpoint(endpoint_path)
    heldout, _rows, _audits = validate_heldout_manifest(
        heldout_path,
        expected_file_sha256=str(heldout_record.get("file_sha256", "")),
        expected_body_sha256=str(heldout_record.get("body_sha256", "")),
    )
    collector, source_rows = validate_collector_bundle(collector_path)
    if (
        launch.get("schema") != LAUNCH_SCHEMA
        or len(launch.get("launches") or []) != 4
        or launch.get("execution_mode") != "single_arm_completion"
        or launch.get("endpoint_manifest_sha256") != endpoint["receipt_sha256"]
        or prereg.get("schema") != PREREG_SCHEMA
        or prereg.get("status") != "rendered_before_candidate_outcomes"
        or prereg.get("outcomes_read_during_render") is not False
        or prereg.get("collector_results_read_during_render") is not False
        or prereg.get("collector_traces_read_during_render") is not False
        or prereg.get("development_gate_only") is not True
        or prereg.get("part_of_sealed_final_evaluation") is not False
        or prereg.get("candidate_only_from_first_decision") is not True
        or prereg.get("interactive_teacher_proxy_used") is not False
        or prereg.get("training_rows_read") != 0
        or prereg.get("heldout_teacher_manifest_validated") is not True
        or prereg.get("heldout_teacher_rows_read_for_validation") != 24
        or prereg.get("heldout_teacher_targets_used_for_training") is not False
        or prereg.get("heldout_teacher_targets_used_to_modify_harness") is not False
        or prereg.get("cells") != 4
        or prereg.get("gate_definition") != GATE_DEFINITION
        or prereg.get("launch")
        != _artifact(path, "launch_manifest_sha256")
        or endpoint_record != _artifact(endpoint_path, "receipt_sha256")
        or heldout_record != _artifact(heldout_path, "manifest_sha256")
        or collector_record
        != {
            "path": str(collector_path),
            "file_sha256": COLLECTOR_BUNDLE_FILE_SHA256,
            "body_sha256": COLLECTOR_BUNDLE_BODY_SHA256,
            "git_sha": COLLECTOR_GIT_SHA,
        }
        or collector.get("bundle_sha256") != COLLECTOR_BUNDLE_BODY_SHA256
        or heldout.get("status") != "ok"
    ):
        raise IntegrityError("macro launch/preregistration policy changed")
    seen: set[str] = set()
    projections: list[dict[str, Any]] = []
    for launch_row in launch["launches"]:
        config_path = Path(str(launch_row["config"])).resolve()
        config = read_json(config_path)
        variant = _variant(config)
        source_row = source_rows[variant]
        source = source_row["config_value"]
        audit = config.get("audit_contract") or {}
        if (
            variant in seen
            or config.get("block_seed") != HOLDOUT_CELLS[variant]["block_seed"]
            or source_row["block_seed"] != config.get("block_seed")
            or source_row["source_value"].get("block_seed") == config.get("block_seed")
            or _model_facing_contract(config) != _model_facing_contract(source)
            or config.get("model") != endpoint["model_spec"]
            or audit.get("candidate_only_from_first_decision") is not True
            or audit.get("interactive_teacher_proxy_used") is not False
            or audit.get("training_row") is not False
            or audit.get("sealed_r4_seed_used") is not False
            or audit.get("collector_git_sha") != COLLECTOR_GIT_SHA
            or audit.get("collector_config_sha256")
            != HOLDOUT_CELLS[variant]["config_sha256"]
            or audit.get("collector_h12_block_seed")
            != HOLDOUT_CELLS[variant]["block_seed"]
        ):
            raise IntegrityError("macro config changed its h12 projection or candidate route")
        seen.add(variant)
        projections.append(
            {
                "variant": variant,
                "source_config": source_row["config"],
                "source_config_sha256": source_row["config_sha256"],
                "candidate_config": str(config_path),
                "candidate_config_sha256": sha256_file(config_path),
                "block_seed": config["block_seed"],
                "model_facing_projection_sha256": sha256_bytes(
                    canonical_bytes(_model_facing_contract(config))
                ),
                "model_facing_harness_unchanged": True,
            }
        )
    if seen != set(VARIANTS) or prereg.get("projections") != projections:
        raise IntegrityError("macro four-cell projection inventory changed")
    return launch, prereg


def _is_cart(url: Any) -> bool:
    try:
        return urlsplit(str(url)).path.rstrip("/") == "/gp/cart"
    except ValueError:
        return False


def _is_checkout(url: Any) -> bool:
    try:
        return urlsplit(str(url)).path.startswith("/gp/buy/")
    except ValueError:
        return False


def _first(steps: list[Mapping[str, Any]], predicate: Any) -> int | None:
    return next((index + 1 for index, step in enumerate(steps) if predicate(step)), None)


def _terminal_run(launch_row: Mapping[str, Any]) -> dict[str, Any]:
    result = Path(str(launch_row["results"])).resolve()
    summary_path = result / "summary.json"
    trajectory_path = result / "trajectory.json"
    summary = read_json(summary_path)
    trajectory = read_json(trajectory_path)
    config = read_json(Path(str(launch_row["config"])).resolve())
    classification, reason = classify_marketplace_result(summary, trajectory)
    model = config.get("model") or {}
    identity = {
        "env": config.get("env"),
        "scaffold": config.get("scaffold"),
        "model": model.get("name") if isinstance(model, Mapping) else model,
        "task_id": (config.get("task") or {}).get("task_id"),
        "condition": config.get("condition"),
    }
    if (
        not classification.startswith("behavioral_")
        or any(summary.get(key) != value for key, value in identity.items())
        or any(trajectory.get(key) != value for key, value in identity.items())
    ):
        raise IntegrityError(f"macro run is not a terminal behavioral result: {reason}")
    raw_steps = trajectory.get("steps")
    if not isinstance(raw_steps, list) or any(not isinstance(step, Mapping) for step in raw_steps):
        raise IntegrityError("macro trajectory step sequence is malformed")
    steps = list(raw_steps)
    cart = _first(
        steps,
        lambda step: _is_cart(step.get("url")) or bool(_CART.search(str(step.get("action", "")))),
    )
    delete = _first(
        steps,
        lambda step: _is_cart(step.get("url"))
        and bool(_DELETE.search(str(step.get("action", "")))),
    )
    checkout = _first(
        steps,
        lambda step: _is_checkout(step.get("url"))
        or bool(_CHECKOUT.search(str(step.get("action", "")))),
    )
    order = _first(steps, lambda step: bool(_ORDER.search(str(step.get("action", "")))))
    buy_now_steps = [
        index + 1
        for index, step in enumerate(steps)
        if _BUY_NOW.search(str(step.get("action", "")))
    ]
    evaluation = trajectory.get("evaluation") or {}
    details = evaluation.get("details") or {}
    basket = details.get("basket") or {}
    line_items = basket.get("line_items") or []
    exact_basket = bool(
        isinstance(line_items, list)
        and len(line_items) == 1
        and isinstance(line_items[0], Mapping)
        and line_items[0].get("asin") == HERO
        and line_items[0].get("quantity") == 1
    )
    exact_hero_order = bool(
        evaluation.get("chosen") == HERO
        and evaluation.get("success") is True
        and exact_basket
    )
    macro_complete = bool(
        cart is not None
        and delete is not None
        and checkout is not None
        and order is not None
        and cart <= delete < checkout <= order
    )
    return {
        "run_id": launch_row["run_id"],
        "variant": _variant(config),
        "block_seed": config["block_seed"],
        "config_sha256": launch_row["config_sha256"],
        "summary_path": str(summary_path),
        "summary_sha256": sha256_file(summary_path),
        "trajectory_path": str(trajectory_path),
        "trajectory_sha256": sha256_file(trajectory_path),
        "classification": classification,
        "chosen": evaluation.get("chosen"),
        "exact_hero50_order": exact_hero_order,
        "final_basket_asins": [
            item.get("asin") for item in line_items if isinstance(item, Mapping)
        ],
        "buy_now_action_steps": buy_now_steps,
        "buy_now_actions": len(buy_now_steps),
        "macro_steps": {
            "cart": cart,
            "delete_on_cart": delete,
            "checkout": checkout,
            "place_order": order,
        },
        "ordered_macro_complete": macro_complete,
    }


def _compute_report(root: Path) -> dict[str, Any]:
    root = root.resolve()
    launch, prereg = audit_launch(root / "launch_manifest.json")
    status_path = root / "executor_state" / "batch_status.json"
    status = read_json(status_path)
    if (
        status.get("launch_manifest_sha256") != launch["launch_manifest_sha256"]
        or status.get("success") is not True
        or status.get("counts") != {"complete": 4}
    ):
        raise IntegrityError("macro executor has not completed exactly four valid runs")
    runs = [_terminal_run(row) for row in launch["launches"]]
    runs.sort(key=lambda row: VARIANTS.index(str(row["variant"])))
    hero_orders = sum(bool(row["exact_hero50_order"]) for row in runs)
    buy_now = sum(int(row["buy_now_actions"]) for row in runs)
    macro = sum(bool(row["ordered_macro_complete"]) for row in runs)
    checks = {
        "exact_hero50_orders": hero_orders
        >= GATE_DEFINITION["exact_hero50_orders_required"],
        "buy_now_actions": buy_now <= GATE_DEFINITION["buy_now_actions_at_most"],
        "ordered_macro_every_run": macro == GATE_DEFINITION["runs"],
    }
    core = {
        "schema": REPORT_SCHEMA,
        "status": "complete",
        "development_gate_only": True,
        "part_of_sealed_final_evaluation": False,
        "candidate_only_from_first_decision": True,
        "interactive_teacher_proxy_used": False,
        "training_rows_read": 0,
        "heldout_teacher_manifest_validated": True,
        "heldout_teacher_rows_read_for_validation": 24,
        "heldout_teacher_targets_used_for_training": False,
        "heldout_teacher_targets_used_to_modify_harness": False,
        "collector_results_used": False,
        "preregistration_sha256": prereg["preregistration_sha256"],
        "launch_manifest_sha256": launch["launch_manifest_sha256"],
        "executor_status": {
            "path": str(status_path),
            "sha256": sha256_file(status_path),
        },
        "gate": {
            "definition": GATE_DEFINITION,
            "totals": {
                "runs": 4,
                "exact_hero50_orders": hero_orders,
                "buy_now_actions": buy_now,
                "ordered_macro_complete": macro,
            },
            "checks": checks,
            "passed": all(checks.values()),
        },
        "runs": runs,
    }
    return {**core, "report_sha256": sha256_bytes(canonical_bytes(core))}


def finalize(arguments: argparse.Namespace) -> None:
    root = arguments.output_root.resolve()
    report = _compute_report(root)
    write_json_create_only(root / "report.json", report)
    print(
        json.dumps(
            {
                "status": "complete",
                "report_sha256": report["report_sha256"],
                "gate_passed": report["gate"]["passed"],
                "totals": report["gate"]["totals"],
            },
            sort_keys=True,
        )
    )


def audit_report(arguments: argparse.Namespace) -> None:
    root = arguments.output_root.resolve()
    path = root / "report.json"
    value = read_json(path)
    _self_hash(value, "report_sha256", "macro report")
    expected = _compute_report(root)
    if value != expected:
        raise IntegrityError("macro report differs from terminal four-cell evidence")
    if arguments.require_pass and value["gate"]["passed"] is not True:
        raise IntegrityError("closed-loop holdout macro development gate did not pass")
    print(
        json.dumps(
            {
                "valid": True,
                "report_sha256": value["report_sha256"],
                "gate_passed": value["gate"]["passed"],
            },
            sort_keys=True,
        )
    )


def audit_launch_command(arguments: argparse.Namespace) -> None:
    launch, prereg = audit_launch(arguments.path)
    print(
        json.dumps(
            {
                "valid": True,
                "runs": 4,
                "launch_manifest_sha256": launch["launch_manifest_sha256"],
                "preregistration_sha256": prereg["preregistration_sha256"],
            },
            sort_keys=True,
        )
    )


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    commands = root.add_subparsers(dest="command", required=True)
    render_parser = commands.add_parser("render")
    render_parser.add_argument("--endpoint-receipt", type=Path, required=True)
    render_parser.add_argument("--expected-endpoint-file-sha256", required=True)
    render_parser.add_argument("--expected-endpoint-body-sha256", required=True)
    render_parser.add_argument("--heldout-manifest", type=Path, required=True)
    render_parser.add_argument("--expected-heldout-file-sha256", required=True)
    render_parser.add_argument("--expected-heldout-body-sha256", required=True)
    render_parser.add_argument("--collector-bundle", type=Path, required=True)
    render_parser.add_argument("--output-root", type=Path, required=True)
    render_parser.add_argument("--base-port", type=int, default=18650)
    render_parser.add_argument(
        "--python-executable", type=Path, default=Path(sys.executable)
    )
    audit_parser = commands.add_parser("audit-launch")
    audit_parser.add_argument("--path", type=Path, required=True)
    finish = commands.add_parser("finalize")
    finish.add_argument("--output-root", type=Path, required=True)
    report = commands.add_parser("audit-report")
    report.add_argument("--output-root", type=Path, required=True)
    report.add_argument("--require-pass", action="store_true")
    return root


def main() -> None:
    arguments = parser().parse_args()
    try:
        {
            "render": render,
            "audit-launch": audit_launch_command,
            "finalize": finalize,
            "audit-report": audit_report,
        }[arguments.command](arguments)
    except (IntegrityError, KeyError, OSError) as exc:
        raise SystemExit(f"InteractiveSolDaggerHeldoutMacroError: {exc}") from exc


if __name__ == "__main__":
    main()
