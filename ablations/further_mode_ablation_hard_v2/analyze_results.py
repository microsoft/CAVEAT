#!/usr/bin/env python3
"""Fail-closed paired analysis for the frozen 112-run hard-v2 campaign.

The complete statistical implementation is intentionally separate from the
launcher so its source is hash-bound before any measured run starts.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import math
import os
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Mapping, Sequence


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MAX_FROZEN_ATTEMPTS = 3


class AnalysisError(ValueError):
    pass


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_object(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise AnalysisError(f"{label} is absent or malformed: {path}") from exc
    if not isinstance(value, dict):
        raise AnalysisError(f"{label} is not a JSON object: {path}")
    return value


def _validate_report_bundle(report_path: Path) -> dict:
    """Validate the create-only JSON/Markdown report hash bundle."""

    report_path = report_path.resolve()
    if report_path.parent.name != "reports" or report_path.suffix != ".json":
        raise AnalysisError("report must be a campaign reports/*.json artifact")
    report = _read_object(report_path, "campaign report")
    markdown = report_path.with_suffix(".md")
    sidecar_path = report_path.with_suffix(".sha256.json")
    sidecar = _read_object(sidecar_path, "report hash sidecar")
    if not markdown.is_file():
        raise AnalysisError("report Markdown companion is absent")
    expected = {
        "json": {"path": report_path.name, "sha256": _sha_file(report_path)},
        "markdown": {"path": markdown.name, "sha256": _sha_file(markdown)},
    }
    if sidecar != expected:
        raise AnalysisError("report JSON/Markdown hash sidecar is invalid")
    return report


def _validate_manifest_bundle(campaign_dir: Path, report: Mapping) -> dict:
    manifest_path = campaign_dir / "campaign_manifest.json"
    sidecar_path = campaign_dir / "campaign_manifest.sha256.json"
    manifest = _read_object(manifest_path, "campaign manifest")
    sidecar = _read_object(sidecar_path, "campaign-manifest hash sidecar")
    expected = {"path": manifest_path.name, "sha256": _sha_file(manifest_path)}
    if sidecar != expected:
        raise AnalysisError("campaign-manifest hash sidecar is invalid")
    if report.get("manifest_sha256") != expected["sha256"]:
        raise AnalysisError("report/manifest hash binding is invalid")
    if report.get("campaign_id") != manifest.get("campaign_id"):
        raise AnalysisError("report/manifest campaign identity differs")
    return manifest


def _validate_analyzer_binding(manifest: Mapping) -> None:
    inventory = manifest.get("campaign_source_inventory")
    if not isinstance(inventory, dict):
        raise AnalysisError("frozen campaign-source inventory is absent")
    expected_inventory_sha = hashlib.sha256(_json_bytes(inventory)).hexdigest()
    if manifest.get("campaign_source_inventory_sha256") != expected_inventory_sha:
        raise AnalysisError("frozen campaign-source inventory digest is invalid")
    source = Path(__file__).resolve()
    expected = inventory.get(source.name)
    actual = {"sha256": _sha_file(source), "size": source.stat().st_size}
    if expected != actual:
        raise AnalysisError("analyzer source differs from the pre-run frozen inventory")


REPORT_IDENTITY_FIELDS = (
    "run_id", "block", "repeat", "study", "arm_id", "harness_arm",
    "scenario", "variant", "condition", "objective_order", "scaffold",
    "primary_region",
)


def _campaign_file(campaign_dir: Path, relative: object, label: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise AnalysisError(f"{label} relative path is absent")
    path = (campaign_dir / relative).resolve()
    if campaign_dir != path and campaign_dir not in path.parents:
        raise AnalysisError(f"{label} escapes the frozen campaign directory")
    if not path.is_file():
        raise AnalysisError(f"{label} is absent: {relative}")
    return path


def _validate_report_schedule(
    report: Mapping, manifest: Mapping, campaign_dir: Path,
) -> list[dict]:
    """Bind every reported run to its exact frozen row and evidence files."""

    schedule = manifest.get("schedule")
    rows = report.get("runs")
    if not isinstance(schedule, list) or not isinstance(rows, list):
        raise AnalysisError("manifest schedule or report runs are malformed")
    if len(schedule) != 112 or len(rows) != 112:
        raise AnalysisError("report and manifest must contain exactly 112 runs")
    if not all(isinstance(row, dict) for row in (*schedule, *rows)):
        raise AnalysisError("schedule/report run entries must be objects")
    scheduled = {row.get("run_id"): row for row in schedule}
    reported = {row.get("run_id"): row for row in rows}
    if (
        None in scheduled or None in reported
        or len(scheduled) != 112 or len(reported) != 112
        or set(scheduled) != set(reported)
    ):
        raise AnalysisError("report run IDs differ from the exact frozen schedule")

    evidence_fields = {
        "summary_sha256": "summary_relpath",
        "trajectory_sha256": "trajectory_relpath",
        "run_log_sha256": "run_log_relpath",
        "launcher_log_sha256": "launcher_log_relpath",
    }
    for run_id, frozen in scheduled.items():
        observed = reported[run_id]
        for field in REPORT_IDENTITY_FIELDS:
            if observed.get(field) != frozen.get(field):
                raise AnalysisError(f"{run_id}: reported {field} differs from schedule")
        for digest_field, path_field in evidence_fields.items():
            path = _campaign_file(
                campaign_dir, frozen.get(path_field), f"{run_id} {path_field}"
            )
            if observed.get(digest_field) != _sha_file(path):
                raise AnalysisError(f"{run_id}: {digest_field} binding is invalid")
        receipt_path = _campaign_file(
            campaign_dir,
            f"launch_receipts/{run_id}.json",
            f"{run_id} launch receipt",
        )
        if observed.get("launch_receipt_sha256") != _sha_file(receipt_path):
            raise AnalysisError(f"{run_id}: launch-receipt hash binding is invalid")
        receipt = _read_object(receipt_path, f"{run_id} launch receipt")
        if receipt.get("run_id") != run_id or receipt.get("row") != frozen:
            raise AnalysisError(f"{run_id}: launch receipt does not bind the frozen row")
        attempt = observed.get("attempt")
        if (
            not isinstance(attempt, int)
            or isinstance(attempt, bool)
            or not 1 <= attempt <= MAX_FROZEN_ATTEMPTS
            or receipt.get("attempt") != attempt
        ):
            raise AnalysisError(
                f"{run_id}: reported attempt does not match the launch receipt"
            )
        terminal_path = _campaign_file(
            campaign_dir,
            f"attempt_terminals/{run_id}_attempt_{attempt}.json",
            f"{run_id} terminal receipt",
        )
        terminal_hash_path = _campaign_file(
            campaign_dir,
            f"attempt_terminals/{run_id}_attempt_{attempt}.sha256.json",
            f"{run_id} terminal-receipt hash sidecar",
        )
        terminal_hash = _read_object(
            terminal_hash_path, f"{run_id} terminal-receipt hash sidecar"
        )
        if terminal_hash != {
            "path": terminal_path.name,
            "sha256": _sha_file(terminal_path),
        }:
            raise AnalysisError(
                f"{run_id}: terminal-receipt hash sidecar is invalid"
            )
        terminal = _read_object(terminal_path, f"{run_id} terminal receipt")
        if (
            terminal.get("kind")
            != "further_mode_ablation_hard_v2_attempt_terminal"
            or terminal.get("run_id") != run_id
            or terminal.get("attempt") != attempt
            or terminal.get("row_sha256")
            != hashlib.sha256(_json_bytes(frozen)).hexdigest()
            or terminal.get("process_ended") is not True
            or terminal.get("launch_receipt_sha256") != _sha_file(receipt_path)
            or observed.get("terminal_receipt_sha256")
            != _sha_file(terminal_path)
            or observed.get("terminal_receipt_hash_sha256")
            != _sha_file(terminal_hash_path)
        ):
            raise AnalysisError(
                f"{run_id}: reported terminal receipt binding is invalid"
            )
    return [dict(row) for row in rows]


def _validate_audit_statuses(report: Mapping, rows: Sequence[Mapping]) -> None:
    exception_count = report.get(
        "pre_agent_compiler_capability_audit_exceptions"
    )
    if (
        report.get("all_applicable_safety_and_lossy_limit_touches_zero")
        is not True
        or report.get("all_applicable_evaluate_result_bounds_not_near")
        is not True
        or not isinstance(exception_count, int)
        or isinstance(exception_count, bool)
        or exception_count < 0
    ):
        raise AnalysisError("applicable limit-audit report contract is invalid")
    statuses = Counter(row.get("limit_audit_status") for row in rows)
    allowed = {
        "complete_and_untouched",
        "not_initialized_pre_agent_compiler_capability_failure",
    }
    if set(statuses) - allowed or statuses.get(
        "not_initialized_pre_agent_compiler_capability_failure", 0
    ) != exception_count or sum(statuses.values()) != 112:
        raise AnalysisError("per-run limit-audit statuses disagree with report")
    for row in rows:
        if row.get("limit_audit_status") != \
                "not_initialized_pre_agent_compiler_capability_failure":
            continue
        classification = row.get("terminal_infrastructure_classification") or {}
        scores = (row.get("preservation_strict"), row.get("strict_binary"))
        score_zero = all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            and float(value) == 0.0
            for value in scores
        )
        if (
            classification.get("class") != "capability"
            or classification.get("code")
            != "compiler_semantic_or_output_failure"
            or row.get("behavioral_failure_zero") is not True
            or not score_zero
        ):
            raise AnalysisError(
                f"{row.get('run_id')}: pre-agent audit exception is not a "
                "compiler capability score-zero run"
            )


def _validate_frozen_input(
    campaign_dir: Path, manifest: Mapping, relative: str,
) -> Path:
    root_relative = manifest.get("frozen_artifact_root")
    if not isinstance(root_relative, str) or not root_relative:
        raise AnalysisError("frozen artifact root is absent")
    path = _campaign_file(
        campaign_dir, f"{root_relative}/{relative}", f"frozen {relative}"
    )
    inventory = manifest.get("frozen_artifact_inventory")
    expected = inventory.get(relative) if isinstance(inventory, dict) else None
    actual = {"sha256": _sha_file(path), "size": path.stat().st_size}
    if expected != actual:
        raise AnalysisError(f"frozen {relative} differs from manifest inventory")
    return path


def exact_sign_flip_p(differences: Sequence[float]) -> float:
    """Exact two-sided blocked sign-randomization p-value."""
    values = [Decimal(str(value)) for value in differences]
    if not values:
        raise AnalysisError("sign-flip test has no blocks")
    observed = abs(sum(values))
    extreme = 0
    total = 1 << len(values)
    for signs in itertools.product((-1, 1), repeat=len(values)):
        statistic = abs(sum(value * sign for value, sign in zip(values, signs)))
        extreme += statistic >= observed
    return extreme / total


def holm_adjust(p_values: Mapping[str, float]) -> dict[str, float]:
    ordered = sorted(p_values.items(), key=lambda item: (item[1], item[0]))
    adjusted = {}
    running = 0.0
    count = len(ordered)
    for index, (name, value) in enumerate(ordered):
        running = max(running, min(1.0, (count - index) * value))
        adjusted[name] = running
    return {name: adjusted[name] for name in p_values}


def _binomial_cdf(k: int, n: int, probability: float) -> float:
    return sum(
        math.comb(n, index) * probability**index
        * (1 - probability) ** (n - index)
        for index in range(k + 1)
    )


def _binomial_upper(k: int, n: int, probability: float) -> float:
    return sum(
        math.comb(n, index) * probability**index
        * (1 - probability) ** (n - index)
        for index in range(k, n + 1)
    )


def clopper_pearson(k: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    if not 0 <= k <= n or n <= 0:
        raise AnalysisError("invalid binomial count")
    if k == 0:
        lower = 0.0
    else:
        lo, hi = 0.0, k / n
        for _ in range(100):
            mid = (lo + hi) / 2
            if _binomial_upper(k, n, mid) > alpha / 2:
                hi = mid
            else:
                lo = mid
        lower = (lo + hi) / 2
    if k == n:
        upper = 1.0
    else:
        lo, hi = k / n, 1.0
        for _ in range(100):
            mid = (lo + hi) / 2
            if _binomial_cdf(k, n, mid) > alpha / 2:
                lo = mid
            else:
                hi = mid
        upper = (lo + hi) / 2
    return lower, upper


def conservative_rd_interval(treatment: Sequence[float], control: Sequence[float]) -> list[float]:
    # Bonferroni-combined 97.5% exact intervals yield at least 95% coverage
    # for their difference without relying on asymptotics.
    ti = clopper_pearson(sum(int(value) for value in treatment), len(treatment), alpha=0.025)
    ci = clopper_pearson(sum(int(value) for value in control), len(control), alpha=0.025)
    return [ti[0] - ci[1], ti[1] - ci[0]]


def _index(rows: Sequence[Mapping], arm: str) -> dict[int, Mapping]:
    selected = {int(row["repeat"]): row for row in rows if row["arm_id"] == arm}
    if set(selected) != set(range(1, 9)):
        raise AnalysisError(f"arm {arm} does not contain exact repetitions 1..8")
    return selected


def _linear_effect(
    rows: Sequence[Mapping],
    name: str,
    family: str,
    coefficients: Mapping[str, float],
    endpoint: str,
) -> dict:
    indexes = {arm: _index(rows, arm) for arm in coefficients}
    blocks = []
    for repeat in range(1, 9):
        blocks.append(sum(
            coefficient * float(indexes[arm][repeat][endpoint])
            for arm, coefficient in coefficients.items()
        ))
    result = {
        "name": name,
        "family": family,
        "endpoint": endpoint,
        "coefficients": dict(coefficients),
        "n_blocks": 8,
        "block_effects": blocks,
        "estimate": sum(blocks) / len(blocks),
        "exact_sign_flip_p_two_sided": exact_sign_flip_p(blocks),
    }
    if endpoint in {"hero", "strict_binary"} and len(coefficients) == 2 and set(coefficients.values()) == {1.0, -1.0}:
        treatment_arm = next(arm for arm, coefficient in coefficients.items() if coefficient == 1)
        control_arm = next(arm for arm, coefficient in coefficients.items() if coefficient == -1)
        treatment = [float(indexes[treatment_arm][repeat][endpoint]) for repeat in range(1, 9)]
        control = [float(indexes[control_arm][repeat][endpoint]) for repeat in range(1, 9)]
        result["conservative_exact_rd_ci95"] = conservative_rd_interval(treatment, control)
    return result


CONTRASTS = {
    "standard_structure": {
        "substrate-clean": {"abl-substrate": 1, "clean": -1},
        "pin-substrate": {"abl-pin-d0": 1, "abl-substrate": -1},
        "bury-substrate": {"abl-bury-d23": 1, "abl-substrate": -1},
        "pin_bury_interaction": {
            "abl-place-d23": 1, "abl-pin-d0": -1,
            "abl-bury-d23": -1, "abl-substrate": 1,
        },
    },
    "standard_visible_cues": {
        **{f"{arm}-place23": {arm: 1, "abl-place-d23": -1}
           for arm in ("sponsored", "ranking", "promo", "trust", "scarcity")},
    },
    "standard_transaction": {
        "drip-place23": {"drip": 1, "abl-place-d23": -1},
        "addon-place23": {"addon": 1, "abl-place-d23": -1},
    },
    "standard_depth_stack": {
        "friction-place30": {"friction": 1, "abl-place-d30": -1},
        "place30-place23": {"abl-place-d30": 1, "abl-place-d23": -1},
        "place52-place23": {"abl-place-d52": 1, "abl-place-d23": -1},
        "combined-place52": {"combined": 1, "abl-place-d52": -1},
    },
    "harness_stage_confirmatory": {
        "P-B": {"P_combined_original": 1, "B_combined_original": -1},
        "K-P": {"K_combined_original": 1, "P_combined_original": -1},
        "E-K": {"E_combined_original": 1, "K_combined_original": -1},
        "D-E": {"D_combined_original": 1, "E_combined_original": -1},
        "A-D": {"A_combined_original": 1, "D_combined_original": -1},
        "F-A": {"F_combined_original": 1, "A_combined_original": -1},
    },
    "harness_secondary": {
        "C-P": {"C_combined_original": 1, "P_combined_original": -1},
        "F-D": {"F_combined_original": 1, "D_combined_original": -1},
    },
    "hard_steering_context": {
        "B_combined-B_clean": {"B_combined_original": 1, "B_clean_original": -1},
        "B_merchandising-B_clean": {"B_merchandising_original": 1, "B_clean_original": -1},
        "B_combined-B_merchandising": {"B_combined_original": 1, "B_merchandising_original": -1},
        "B_format_only-B_clean": {"B_format_only_original": 1, "B_clean_original": -1},
    },
    "hard_harness_context": {
        "F_clean-B_clean": {"F_clean_original": 1, "B_clean_original": -1},
        "F_combined-B_combined": {"F_combined_original": 1, "B_combined_original": -1},
        "harness_by_steering_interaction": {
            "F_combined_original": 1, "B_combined_original": -1,
            "F_clean_original": -1, "B_clean_original": 1,
        },
    },
    "hard_objective_order": {
        "B_original-B_reversed": {"B_combined_original": 1, "B_combined_reversed": -1},
        "F_original-F_reversed": {"F_combined_original": 1, "F_combined_reversed": -1},
        "harness_by_order_interaction": {
            "F_combined_original": 1, "F_combined_reversed": -1,
            "B_combined_original": -1, "B_combined_reversed": 1,
        },
    },
}
ENDPOINTS = ("preservation_strict", "hero", "strict_binary")


def _product_value(product: Mapping, field: str):
    if field in product:
        return product[field]
    for container in (product.get("specs"), product.get("tech")):
        if isinstance(container, dict) and field in container:
            return container[field]
    return None


def _threshold_satisfied(product: Mapping, threshold: Mapping) -> bool:
    key = str(threshold.get("key") or "")
    expected = threshold.get("value")
    operators = (
        ("__min", "ge"), ("__max", "le"), ("__lt", "lt"),
        ("__gt", "gt"), ("__lte", "le"), ("__gte", "ge"),
        ("__eq", "eq"), ("__ne", "ne"),
    )
    field, operator = key, "eq"
    for suffix, candidate in operators:
        if key.endswith(suffix):
            field, operator = key[:-len(suffix)], candidate
            break
    observed = _product_value(product, field)
    if operator in {"ge", "le", "lt", "gt"}:
        if isinstance(observed, bool) or not isinstance(observed, (int, float)):
            return False
        if isinstance(expected, bool) or not isinstance(expected, (int, float)):
            raise AnalysisError(f"malformed numeric threshold: {key}")
        return {
            "ge": observed >= expected, "le": observed <= expected,
            "lt": observed < expected, "gt": observed > expected,
        }[operator]
    return observed != expected if operator == "ne" else observed == expected


def _rank_metrics(pool: Sequence[Mapping], preferences: Mapping,
                  chosen: object) -> dict:
    objectives = preferences.get("graded")
    thresholds = preferences.get("thresholds")
    if not isinstance(objectives, list) or len(objectives) != 2 \
            or not isinstance(thresholds, list):
        raise AnalysisError("hard rank endpoint requires two axes and frozen thresholds")
    feasible = [
        product for product in pool
        if all(_threshold_satisfied(product, threshold) for threshold in thresholds)
    ]
    if not feasible:
        raise AnalysisError("hard frozen pool has no feasible products")
    by_asin = {product.get("asin"): product for product in pool}
    selected = by_asin.get(chosen) if isinstance(chosen, str) else None
    selected_feasible = selected in feasible
    metrics = {"feasible_count": len(feasible), "chosen_feasible": int(selected_feasible)}
    for index, objective in enumerate(objectives, 1):
        attr = objective.get("attr")
        direction = objective.get("direction")
        if not isinstance(attr, str) or direction not in {"lower", "higher"}:
            raise AnalysisError("hard objective axis is malformed")
        values = [float(_product_value(product, attr)) for product in feasible]
        raw = None
        if selected_feasible:
            value = float(_product_value(selected, attr))
            raw = 1 + sum(
                other < value - 1e-12 if direction == "lower"
                else other > value + 1e-12
                for other in values
            )
        penalized = raw if raw is not None else len(feasible) + 1
        metrics.update({
            f"axis{index}_attr": attr,
            f"axis{index}_direction": direction,
            f"axis{index}_rank": raw,
            f"axis{index}_rank_penalized": penalized,
            f"axis{index}_rank_fraction": (penalized - 1) / len(feasible),
        })
    return metrics


def _directional_rank_effects(rows: Sequence[Mapping]) -> list[dict]:
    by_arm = {arm: _index(rows, arm) for arm in (
        "B_combined_original", "B_combined_reversed",
        "F_combined_original", "F_combined_reversed",
    )}
    blocks_by_harness = {}
    for harness in ("B", "F"):
        blocks = []
        for repeat in range(1, 9):
            original = by_arm[f"{harness}_combined_original"][repeat]
            reversed_row = by_arm[f"{harness}_combined_reversed"][repeat]
            axis1_shift = (
                float(reversed_row["axis1_rank_fraction"])
                - float(original["axis1_rank_fraction"])
            )
            axis2_shift = (
                float(reversed_row["axis2_rank_fraction"])
                - float(original["axis2_rank_fraction"])
            )
            blocks.append((axis1_shift - axis2_shift) / 2)
        blocks_by_harness[harness] = blocks
    specifications = {
        "B_first_mentioned_directional_rank_shift": blocks_by_harness["B"],
        "F_first_mentioned_directional_rank_shift": blocks_by_harness["F"],
        "harness_attenuation_of_directional_rank_shift": [
            f_value - b_value for f_value, b_value in zip(
                blocks_by_harness["F"], blocks_by_harness["B"]
            )
        ],
    }
    effects = []
    for name, blocks in specifications.items():
        effects.append({
            "name": name,
            "family": "hard_objective_order_directional_rank",
            "endpoint": "signed_first_mentioned_objective_ITT_normalized_rank_advantage",
            "n_blocks": 8,
            "block_effects": blocks,
            "estimate": sum(blocks) / 8,
            "exact_sign_flip_p_two_sided": exact_sign_flip_p(blocks),
        })
    adjusted = holm_adjust({
        effect["name"]: effect["exact_sign_flip_p_two_sided"]
        for effect in effects
    })
    for effect in effects:
        effect["holm_p_within_frozen_family_and_endpoint"] = adjusted[effect["name"]]
    return effects


HARD_CONTRAST_FAMILIES = (
    "harness_stage_confirmatory",
    "harness_secondary",
    "hard_steering_context",
    "hard_harness_context",
    "hard_objective_order",
)


def compute_effects(rows: Sequence[Mapping], *, include_directional_rank: bool = False) -> dict:
    if len(rows) != 112 or len({row.get("run_id") for row in rows}) != 112:
        raise AnalysisError("analysis requires the exact 112-run hard denominator")
    for row in rows:
        if row.get("hero") not in {0, 1} or row.get("strict_binary") not in {0, 1, 0.0, 1.0}:
            raise AnalysisError("hero and strict_binary endpoints must be exactly binary")
    effects = []
    for family in HARD_CONTRAST_FAMILIES:
        contrasts = CONTRASTS[family]
        for name, coefficients in contrasts.items():
            for endpoint in ENDPOINTS:
                effects.append(_linear_effect(
                    rows, name, family, coefficients, endpoint
                ))
    for family in HARD_CONTRAST_FAMILIES:
        for endpoint in ENDPOINTS:
            selected = [effect for effect in effects if effect["family"] == family and effect["endpoint"] == endpoint]
            adjusted = holm_adjust({
                effect["name"]: effect["exact_sign_flip_p_two_sided"]
                for effect in selected
            })
            for effect in selected:
                effect["holm_p_within_frozen_family_and_endpoint"] = adjusted[effect["name"]]
    if include_directional_rank:
        effects.extend(_directional_rank_effects(rows))
    return {
        "schema_version": 1,
        "kind": "further_mode_ablation_hard_v2_exact_effects",
        "scheduled_denominator": 112,
        "endpoints": list(ENDPOINTS),
        "effects": effects,
    }


PROCESS_ENDPOINTS = (
    "contract_compile_calls", "contract_compile_rejections",
    "contract_compile_failures", "contract_semantically_correct_vs_gold",
    "decision_checkpoint_calls", "decision_checkpoint_rejections",
    "decision_checkpoint_approvals", "first_submission_would_pass_full",
    "last_submission_would_pass_full", "frontier_inspected_count",
    "frontier_advertised_count", "frontier_advertised_page_count",
    "frontier_enumerated_page_count", "frontier_unresolved_count",
    "frontier_exhausted", "frontier_resolved_and_exhausted",
    "approved_matches_final_choice", "auxiliary_calls", "auxiliary_tokens",
    "auxiliary_seconds", "unique_pdp_count", "search_page_count",
    "pdp_to_frontier_ratio", "num_steps", "seconds",
)
PROCESS_PAIRS = (
    ("P-B", "P_combined_original", "B_combined_original"),
    ("K-P", "K_combined_original", "P_combined_original"),
    ("E-K", "E_combined_original", "K_combined_original"),
    ("D-E", "D_combined_original", "E_combined_original"),
    ("A-D", "A_combined_original", "D_combined_original"),
    ("F-A", "F_combined_original", "A_combined_original"),
    ("C-P", "C_combined_original", "P_combined_original"),
    ("F-D", "F_combined_original", "D_combined_original"),
)


def _process_value(row: Mapping, endpoint: str):
    if endpoint == "num_steps" or endpoint == "seconds":
        return row.get(endpoint)
    return (row.get("process_endpoints") or {}).get(endpoint)


def compute_process_descriptives(rows: Sequence[Mapping]) -> dict:
    hard_rows = [row for row in rows if row.get("scenario") == "laptop_hard"]
    arm_aggregates = []
    for arm in sorted({row["arm_id"] for row in hard_rows}):
        selected = [row for row in hard_rows if row["arm_id"] == arm]
        endpoints = {}
        for endpoint in PROCESS_ENDPOINTS:
            values = [_process_value(row, endpoint) for row in selected]
            numeric = [float(value) for value in values
                       if isinstance(value, (int, float, bool))]
            endpoints[endpoint] = {
                "n_observed": len(numeric),
                "mean": sum(numeric) / len(numeric) if numeric else None,
            }
        arm_aggregates.append({"arm_id": arm, "n_runs": len(selected),
                               "endpoints": endpoints})
    paired = []
    for contrast, treatment, control in PROCESS_PAIRS:
        treatment_rows = _index(hard_rows, treatment)
        control_rows = _index(hard_rows, control)
        for endpoint in PROCESS_ENDPOINTS:
            blocks = []
            for repeat in range(1, 9):
                left = _process_value(treatment_rows[repeat], endpoint)
                right = _process_value(control_rows[repeat], endpoint)
                if not isinstance(left, (int, float, bool)) \
                        or not isinstance(right, (int, float, bool)):
                    blocks = []
                    break
                blocks.append(float(left) - float(right))
            if blocks:
                paired.append({
                    "contrast": contrast,
                    "endpoint": endpoint,
                    "n_blocks": 8,
                    "mean_paired_difference": sum(blocks) / 8,
                    "block_differences": blocks,
                })
    return {
        "interpretation": (
            "Prospectively declared process/treatment-uptake descriptions; "
            "not causal mediators and not multiplicity-tested outcomes."
        ),
        "arm_aggregates": arm_aggregates,
        "paired_stage_descriptives": paired,
    }


def _write_new(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise SystemExit(f"refusing to replace analysis output: {path}") from exc
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def analyze(report_path: Path, output: Path) -> dict:
    report_path = report_path.resolve()
    report = _validate_report_bundle(report_path)
    if (
        report.get("kind") != "further_mode_ablation_hard_v2_run_report"
        or report.get("valid_runs") != 112
        or report.get("scheduled_denominator") != 112
    ):
        raise AnalysisError("input is not one complete frozen campaign report")
    campaign_dir = report_path.parents[1]
    manifest_path = campaign_dir / "campaign_manifest.json"
    manifest = _validate_manifest_bundle(campaign_dir, report)
    if (
        manifest.get("kind") != "further_mode_ablation_hard_v2_campaign"
        or manifest.get("design", {}).get("total_runs") != 112
        or report.get("metric_policy") != manifest.get("metric_policy")
    ):
        raise AnalysisError("campaign manifest/report policy identity differs")
    _validate_analyzer_binding(manifest)
    rows = _validate_report_schedule(report, manifest, campaign_dir)
    _validate_audit_statuses(report, rows)

    pool_path = _validate_frozen_input(
        campaign_dir, manifest, "laptop_hard/pool.json"
    )
    preferences_path = _validate_frozen_input(
        campaign_dir, manifest, "laptop_hard/preferences.json"
    )
    pool = json.loads(pool_path.read_text())
    preferences_root = json.loads(preferences_path.read_text())
    if not isinstance(pool, list) or not isinstance(preferences_root, dict) \
            or not isinstance(preferences_root.get("graded"), dict):
        raise AnalysisError("frozen hard pool/preferences are malformed")
    preferences = preferences_root["graded"]
    for row in rows:
        if row.get("scenario") == "laptop_hard":
            row.update(_rank_metrics(pool, preferences, row.get("chosen")))
    result = compute_effects(rows, include_directional_rank=True)
    result["process_descriptives"] = compute_process_descriptives(rows)
    result["rank_endpoint_inputs"] = {
        "pool": {"path": str(pool_path), "sha256": _sha_file(pool_path)},
        "preferences": {
            "path": str(preferences_path), "sha256": _sha_file(preferences_path)
        },
        "invalid_choice_policy": "threshold-infeasible/off-catalog/no-order => N+1; normalized loss=1",
    }
    result["input_report"] = {
        "path": str(report_path), "sha256": _sha_file(report_path),
        "sidecar": {
            "path": str(report_path.with_suffix(".sha256.json")),
            "sha256": _sha_file(report_path.with_suffix(".sha256.json")),
        },
    }
    result["input_manifest"] = {
        "path": str(manifest_path), "sha256": _sha_file(manifest_path),
        "sidecar": {
            "path": str(campaign_dir / "campaign_manifest.sha256.json"),
            "sha256": _sha_file(
                campaign_dir / "campaign_manifest.sha256.json"
            ),
        },
    }
    result["analyzer_sha256"] = _sha_file(Path(__file__).resolve())
    result["frozen_analyzer_inventory_sha256"] = manifest[
        "campaign_source_inventory_sha256"
    ]
    _write_new(output.resolve(), result)
    _write_new(output.resolve().with_suffix(output.suffix + ".sha256.json"), {
        "path": output.name, "sha256": _sha_file(output.resolve())
    })
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    analyze(args.report, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
