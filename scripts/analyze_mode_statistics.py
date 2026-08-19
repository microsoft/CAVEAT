#!/usr/bin/env python3
"""Reproducible quantitative layer for the trajectory-mode evidence study.

The input is the immutable observation census produced by
``analyze_trajectory_modes.py``.  This module deliberately does not import or
mutate the benchmark, storefront, catalog, or browser scaffold.

The archive-wide association analysis is intentionally modest: it uses a
linear-probability model (LPM) with fixed effects for model configuration,
scenario, relative-preference level, and steering condition.  The coefficient
on a binary signal is therefore an adjusted risk difference (and its average
marginal effect).  HC1 uncertainty is reported alongside a crossed
model/scenario pigeonhole cluster bootstrap.  Five scenario clusters are too
few for precise cluster inference; that limitation is emitted in every output.

All six failure-family indicators are deterministic *proxies*, computed only
from trajectory content before a conservative selection cutoff.  They remain
``UNVALIDATED`` until compared with the blinded qualitative hand labels.  In
particular, regex matches are not treated as definitive psychological modes.
Transaction drift itself occurs after selection and is kept descriptive; its
pre-selection proxy is only the absence of an explicit transaction-safeguard
intent.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = REPO_ROOT / "results" / "mixed_method_trajectory_analysis" / "runs.json"
DEFAULT_OUTPUT = REPO_ROOT / "results" / "mode_statistics"
BOOTSTRAP_SEED = 20260805
DEFAULT_BOOTSTRAP_REPS = 999
V19_BOOTSTRAP_REPS = 20_000
Z_975 = 1.959963984540054

PRODUCT_TOKEN_RE = re.compile(r"(?<![A-Z0-9-])(?:EXP-[A-Z0-9-]+|B[A-Z0-9]{9})(?![A-Z0-9-])")
PAGE_RE = re.compile(r"(?:[?&]|&amp;|\\u0026)page=(\d+)", re.I)
SEARCH_RE = re.compile(r"/(?:s|search)(?:[/?#]|$)", re.I)
PRODUCT_API_RE = re.compile(r"/api/products?(?:/asin)?(?:/|\?|$)", re.I)
FILE_RE = re.compile(r"file_name['\"]?\s*:\s*['\"]([^'\"]+)['\"]", re.I)
EXHAUSTIVE_RE = re.compile(
    r"\b(?:all|every)\s+(?:the\s+)?(?:\d+\s+)?(?:reachable\s+|search\s+)?"
    r"(?:result|candidate|option|product)s?\b|\b(?:complete|full|entire|exhaustive)\s+"
    r"(?:search\s+)?(?:set|catalog|market|frontier|result)s?\b|\bexhaust(?:ed|ion)\b",
    re.I,
)
LEXICOGRAPHIC_RE = re.compile(
    r"\b(?:primary|first)\s+(?:ranking|objective|criterion|priority)\b|"
    r"\b(?:secondary|tie[- ]?break(?:er)?)\b|"
    r"\b(?:lightest|longest|highest|lowest|cheapest)\b.{0,100}\bthen\b",
    re.I | re.S,
)
UNKNOWN_RE = re.compile(r"\b(?:unknown|unresolved|missing|conflict(?:ing)?)\b", re.I)
RETRIEVAL_FAILURE_RE = re.compile(
    r"\b(?:fetch|extract|read|request|retrieval|product detail|pdp)\b.{0,100}"
    r"\b(?:failed|failure|error|timed out|timeout|truncated|empty|unavailable)\b|"
    r"\b(?:failed|failure|error|timed out|timeout|truncated)\b.{0,100}"
    r"\b(?:fetch|extract|read|request|retrieval|product detail|pdp)\b",
    re.I | re.S,
)
ERROR_RE = re.compile(
    r"\b(?:validationerror|invalid (?:json|action|tool)|unsupported (?:action|navigation)|"
    r"targetclosederror|api(?:connection|timeout)error|http\s+[45]\d\d|internal server error|"
    r"action (?:failed|error)|tool (?:failed|error))\b",
    re.I,
)
COMPARE_RE = re.compile(r"\b(?:compare|comparison|rank(?:ed|ing)?|frontier|winner)\b", re.I)
TRANSACTION_SAFEGUARD_RE = re.compile(
    r"\b(?:cart|checkout|basket|order review)\b.{0,180}"
    r"\b(?:verify|re[- ]?check|identity|quantity|total|remove|extra|add[- ]?on)\b|"
    r"\b(?:verify|re[- ]?check)\b.{0,180}\b(?:cart|checkout|basket|order review)\b",
    re.I | re.S,
)
ADD_TO_CART_RE = re.compile(
    r"ax_name=['\"](?:add to cart|buy now)['\"]|/api/cart/items|"
    r"\b(?:add_to_cart|add to cart|buy now)\b",
    re.I,
)
FINAL_CHOICE_RE = re.compile(
    r"\b(?:final(?:\s+choice)?|winner|select(?:ed|ing)?|cho(?:se|sen)|best\s+(?:option|candidate)|"
    r"proceed\s+with|will\s+(?:buy|purchase)|decided\s+(?:on|to))\b",
    re.I,
)


FAILURE_PROXY_SPECS: tuple[dict[str, str], ...] = (
    {
        "name": "shortlist_capture_proxy",
        "family": "shortlist_capture",
        "definition": "Before selection, no search page beyond page 1 is observed and fewer than 25 distinct catalog identities are referenced.",
    },
    {
        "name": "premature_satisficing_proxy",
        "family": "premature_satisficing",
        "definition": "A completeness/exhaustion claim occurs before selection while the shortlist-capture proxy is true.",
    },
    {
        "name": "unknown_to_acceptable_proxy",
        "family": "unknown_to_acceptable",
        "definition": "Unknown/unresolved or retrieval-failure language occurs before selection.",
    },
    {
        "name": "instruction_drift_proxy",
        "family": "instruction_drift_objective_collapse",
        "definition": "Lexicographic/primary-secondary language occurs before selection.",
    },
    {
        "name": "transaction_drift_risk_proxy",
        "family": "transaction_drift",
        "definition": "No explicit cart/checkout identity, quantity, total, or extra-item safeguard is stated before selection; this is a risk-intent proxy, not observed transaction drift.",
    },
    {
        "name": "action_unreliability_proxy",
        "family": "action_unreliability",
        "definition": "A deterministic action/tool error or repeated action loop occurs before selection.",
    },
)
FAILURE_PROXY_NAMES = tuple(item["name"] for item in FAILURE_PROXY_SPECS)


class StatisticsValidationError(RuntimeError):
    """The frozen census cannot support the requested analysis."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StatisticsValidationError(f"{name} must be numeric, got {value!r}")
    answer = float(value)
    if not math.isfinite(answer):
        raise StatisticsValidationError(f"{name} is non-finite")
    return answer


def load_census(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise StatisticsValidationError(f"cannot load {path}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("runs"), list):
        raise StatisticsValidationError("input must be a trajectory-census object with a runs list")
    rows = payload["runs"]
    if payload.get("run_count") != len(rows):
        raise StatisticsValidationError("run_count does not match runs length")
    ids = [row.get("run_id") for row in rows]
    if any(not isinstance(item, str) or not item for item in ids) or len(set(ids)) != len(ids):
        raise StatisticsValidationError("run_id values must be nonempty and unique")
    for row in rows:
        if row.get("schema") != "agentarena.mixed-method-run.v1":
            raise StatisticsValidationError(f"unsupported row schema for {row.get('run_id')}")
        for key in ("literal_hero", "strict_binary", "preservation_strict"):
            value = _finite_number(row.get(key), f"{row.get('run_id')}:{key}")
            if not 0 <= value <= 1:
                raise StatisticsValidationError(f"{row.get('run_id')}:{key} outside [0,1]")
    return rows, {
        "path": str(path.resolve()),
        "sha256": _sha256(path),
        "source_snapshot_sha256": payload.get("source_snapshot_sha256"),
        "run_count": len(rows),
    }


def _step_text(step: Mapping[str, Any]) -> str:
    return "\n".join(str(step.get(key) or "") for key in ("action", "reasoning", "note", "url"))


def _ordered_steps(trajectory: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [step for step in (trajectory.get("steps") or []) if isinstance(step, dict)]


def _selection_position(steps: Sequence[Mapping[str, Any]], chosen: Any, chosen_label: Any) -> int:
    """Return a one-based conservative final-choice/cart cutoff.

    A product declaration counts only when the ultimately chosen identity or
    label appears in the same step as explicit final-choice language.  Cart
    intent inside a TODO alone is not a cart action: write/replace-file actions
    are excluded from the cart-action branch.
    """

    chosen_terms = [str(value).strip() for value in (chosen, chosen_label) if str(value or "").strip()]
    for position, step in enumerate(steps, 1):
        text = _step_text(step)
        action = str(step.get("action") or "")
        lower_action = action.lower()
        final_declaration = (
            bool(chosen_terms)
            and any(term.lower() in text.lower() for term in chosen_terms)
            and bool(FINAL_CHOICE_RE.search(text))
        )
        file_only = "write_file" in lower_action or "replace_file" in lower_action
        cart_action = bool(ADD_TO_CART_RE.search(action)) and not file_only
        if final_declaration or cart_action:
            return position
    return len(steps) + 1


def _longest_repeat(actions: Sequence[str]) -> int:
    longest = current = 0
    previous: str | None = None
    for action in actions:
        signature = re.sub(r"\s+", " ", action).strip()
        if not signature:
            continue
        if signature == previous:
            current += 1
        else:
            previous, current = signature, 1
        longest = max(longest, current)
    return longest


def derive_preselection_signals(row: Mapping[str, Any], *, repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    provenance = row.get("provenance") or {}
    raw_path = provenance.get("trajectory_path")
    if not isinstance(raw_path, str):
        raise StatisticsValidationError(f"{row.get('run_id')}: missing trajectory_path")
    trajectory_path = Path(raw_path)
    if not trajectory_path.is_absolute():
        trajectory_path = repo_root / trajectory_path
    if not trajectory_path.is_file():
        raise StatisticsValidationError(f"{row.get('run_id')}: trajectory missing at {trajectory_path}")
    expected_hash = provenance.get("trajectory_sha256")
    if expected_hash and _sha256(trajectory_path) != expected_hash:
        raise StatisticsValidationError(f"{row.get('run_id')}: trajectory hash drift")
    try:
        trajectory = json.loads(trajectory_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise StatisticsValidationError(f"{row.get('run_id')}: invalid trajectory: {exc}") from exc
    if not isinstance(trajectory, dict):
        raise StatisticsValidationError(f"{row.get('run_id')}: trajectory is not an object")

    steps = _ordered_steps(trajectory)
    cutoff = _selection_position(steps, row.get("chosen"), row.get("chosen_label"))
    prefix = steps[: max(cutoff - 1, 0)]
    texts = [_step_text(step) for step in prefix]
    blob = "\n".join(texts)
    lower = blob.lower()
    actions = [str(step.get("action") or "") for step in prefix]

    catalog_ids = set(str(item).upper() for item in (row.get("signals") or {}).get("candidate_ids", []))
    for extra in (row.get("chosen"), row.get("literal_hero_asin")):
        if isinstance(extra, str):
            catalog_ids.add(extra.upper())
    candidate_ids = {
        match.group(0).upper()
        for match in PRODUCT_TOKEN_RE.finditer(blob.upper())
        if match.group(0).upper() in catalog_ids
    }
    pages = {int(value) for value in PAGE_RE.findall(blob)}
    if SEARCH_RE.search(blob):
        pages.add(1)
    file_names = set(FILE_RE.findall("\n".join(actions)))
    plan_names = {name for name in file_names if Path(name).name.lower() in {"todo.md", "plan.md", "task.md"}}
    same_origin = "fetch(" in lower
    product_api = bool(PRODUCT_API_RE.search(blob))
    bulk = same_origin and bool(
        re.search(r"promise\.all|queryselectorall|for\s*\(|\.map\s*\(|batch|bulk", blob, re.I)
        or len(candidate_ids) >= 2
    )
    exhaustive = bool(EXHAUSTIVE_RE.search(blob))
    lexicographic = bool(LEXICOGRAPHIC_RE.search(blob))
    unresolved = bool(UNKNOWN_RE.search(blob) or RETRIEVAL_FAILURE_RE.search(blob))
    action_error = bool(ERROR_RE.search(blob))
    repeated_loop = _longest_repeat(actions) >= 3 or max(Counter(actions).values(), default=0) >= 5
    safeguard = bool(TRANSACTION_SAFEGUARD_RE.search(blob))
    narrow = (max(pages, default=1) <= 1) and len(candidate_ids) < 25

    return {
        "selection_step_position": cutoff if cutoff <= len(steps) else None,
        "preselection_step_count": len(prefix),
        "candidate_id_count": len(candidate_ids),
        "search_page_count": len(pages),
        "max_search_page_seen": max(pages, default=0),
        "product_api_attempt": product_api,
        "same_origin_fetch": same_origin,
        "bulk_detail_fetch": bulk,
        "todo_or_plan_file": bool(plan_names),
        "evidence_file": bool(file_names - plan_names),
        "exhaustive_claim": exhaustive,
        "explicit_comparison_claim": bool(COMPARE_RE.search(blob)),
        "transaction_safeguard_intent": safeguard,
        "shortlist_capture_proxy": narrow,
        "premature_satisficing_proxy": narrow and exhaustive,
        "unknown_to_acceptable_proxy": unresolved,
        "instruction_drift_proxy": lexicographic,
        "transaction_drift_risk_proxy": not safeguard,
        "action_unreliability_proxy": action_error or repeated_loop,
    }


def attach_preselection(rows: Sequence[Mapping[str, Any]], *, repo_root: Path = REPO_ROOT) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        copied = dict(row)
        copied["preselection"] = derive_preselection_signals(row, repo_root=repo_root)
        result.append(copied)
    return result


def _group_outcomes(rows: Sequence[Mapping[str, Any]], keys: Sequence[str]) -> list[dict[str, Any]]:
    groups: defaultdict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[tuple(row.get(key) for key in keys)].append(row)
    output: list[dict[str, Any]] = []
    for values, members in sorted(groups.items(), key=lambda item: tuple(str(v) for v in item[0])):
        measured = [row for row in members if row.get("analysis_included")]
        n = len(measured)
        item = {key: value for key, value in zip(keys, values)}
        item.update(
            {
                "inventory_runs": len(members),
                "infra_excluded": len(members) - n,
                "measured_runs": n,
                "literal_heroes": sum(int(row["literal_hero"]) for row in measured),
                "literal_hero_rate": sum(float(row["literal_hero"]) for row in measured) / n if n else None,
                "strict_successes": sum(int(row["strict_binary"]) for row in measured),
                "strict_success_rate": sum(float(row["strict_binary"]) for row in measured) / n if n else None,
                "mean_preservation_strict": sum(float(row["preservation_strict"]) for row in measured) / n if n else None,
                "transactions": sum(row.get("chosen") is not None for row in measured),
                "behavioral_no_orders": sum(row.get("chosen") is None for row in measured),
                "gate_failures": sum(row.get("G") == 0 for row in measured),
                "postselection_transaction_drift": sum(
                    bool((row.get("basket") or {}).get("hard_constraint_violation"))
                    or int((row.get("basket") or {}).get("extra_line_items") or 0) > 0
                    for row in measured
                ),
            }
        )
        output.append(item)
    return output


def _dummy_matrix(rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> tuple[np.ndarray, list[str]]:
    columns: list[np.ndarray] = [np.ones(len(rows), dtype=float)]
    names = ["intercept"]
    for field in fields:
        levels = sorted({str(row.get(field)) for row in rows})
        for level in levels[1:]:
            columns.append(np.asarray([float(str(row.get(field)) == level) for row in rows]))
            names.append(f"fe:{field}={level}")
    return np.column_stack(columns), names


@dataclass
class LpmFit:
    beta: np.ndarray
    se_hc1: np.ndarray
    rank: int
    n: int
    columns: list[str]


def fit_lpm(y: np.ndarray, x: np.ndarray, columns: Sequence[str], weights: np.ndarray | None = None) -> LpmFit:
    if x.ndim != 2 or y.ndim != 1 or len(y) != len(x):
        raise ValueError("invalid LPM shapes")
    n, p = x.shape
    w = np.ones(n) if weights is None else np.asarray(weights, dtype=float)
    if len(w) != n or np.any(w < 0) or not np.any(w > 0):
        raise ValueError("invalid LPM weights")
    xtwx = x.T @ (w[:, None] * x)
    bread = np.linalg.pinv(xtwx, rcond=1e-12)
    beta = bread @ (x.T @ (w * y))
    residual = y - x @ beta
    meat = x.T @ ((w * residual**2)[:, None] * x)
    rank = int(np.linalg.matrix_rank(x[w > 0]))
    correction = n / max(n - rank, 1)
    covariance = correction * bread @ meat @ bread
    return LpmFit(
        beta=beta,
        se_hc1=np.sqrt(np.maximum(np.diag(covariance), 0)),
        rank=rank,
        n=n,
        columns=list(columns),
    )


def _normal_p(coef: float, se: float) -> float | None:
    if not math.isfinite(se) or se <= 0:
        return None
    return math.erfc(abs(coef / se) / math.sqrt(2.0))


def _cluster_bootstrap_coefficients(
    y: np.ndarray,
    x: np.ndarray,
    columns: Sequence[str],
    model_clusters: Sequence[str],
    scenario_clusters: Sequence[str],
    coefficient_indices: Sequence[int],
    *,
    reps: int,
    seed: int,
) -> np.ndarray:
    """Crossed pigeonhole bootstrap by independently resampling both axes."""

    models = sorted(set(model_clusters))
    scenarios = sorted(set(scenario_clusters))
    if len(models) < 2 or len(scenarios) < 2:
        raise StatisticsValidationError("two-way bootstrap requires >=2 clusters on each axis")
    model_index = {value: index for index, value in enumerate(models)}
    scenario_index = {value: index for index, value in enumerate(scenarios)}
    row_model = np.asarray([model_index[value] for value in model_clusters])
    row_scenario = np.asarray([scenario_index[value] for value in scenario_clusters])
    rng = np.random.default_rng(seed)
    estimates: list[np.ndarray] = []
    for _ in range(reps):
        model_counts = np.bincount(rng.integers(0, len(models), len(models)), minlength=len(models))
        scenario_counts = np.bincount(
            rng.integers(0, len(scenarios), len(scenarios)), minlength=len(scenarios)
        )
        weights = model_counts[row_model] * scenario_counts[row_scenario]
        if np.count_nonzero(weights) <= x.shape[1]:
            continue
        fit = fit_lpm(y, x, columns, weights=weights)
        estimates.append(fit.beta[list(coefficient_indices)])
    if len(estimates) < max(50, reps // 2):
        raise StatisticsValidationError(
            f"only {len(estimates)}/{reps} valid two-way bootstrap replicates"
        )
    return np.asarray(estimates)


def holm_adjust(p_values: Mapping[str, float | None]) -> dict[str, float | None]:
    valid = sorted(
        ((name, float(value)) for name, value in p_values.items() if value is not None),
        key=lambda item: (item[1], item[0]),
    )
    adjusted: dict[str, float | None] = {name: None for name in p_values}
    running = 0.0
    m = len(valid)
    for rank, (name, p_value) in enumerate(valid):
        running = max(running, (m - rank) * p_value)
        adjusted[name] = min(1.0, running)
    return adjusted


def adjusted_proxy_models(
    rows: Sequence[Mapping[str, Any]], *, bootstrap_reps: int, seed: int
) -> dict[str, Any]:
    cohort = [
        row for row in rows
        if row.get("cohort") == "overhaul_non_absolute" and row.get("analysis_included")
    ]
    if not cohort:
        raise StatisticsValidationError("no measured overhaul_non_absolute rows")
    y = np.asarray([float(row["literal_hero"]) for row in cohort])
    controls, control_names = _dummy_matrix(
        cohort, ("model", "scenario", "relative_level", "condition")
    )
    clusters_model = [str(row["model"]) for row in cohort]
    clusters_scenario = [str(row["scenario"]) for row in cohort]
    single_results: list[dict[str, Any]] = []
    raw_cluster_p: dict[str, float | None] = {}

    for signal_index, spec in enumerate(FAILURE_PROXY_SPECS):
        name = spec["name"]
        signal = np.asarray([float(bool(row["preselection"][name])) for row in cohort])
        x = np.column_stack((controls, signal))
        columns = [*control_names, name]
        fit = fit_lpm(y, x, columns)
        coefficient = float(fit.beta[-1])
        hc1_se = float(fit.se_hc1[-1])
        boot = _cluster_bootstrap_coefficients(
            y,
            x,
            columns,
            clusters_model,
            clusters_scenario,
            [-1],
            reps=bootstrap_reps,
            seed=seed + signal_index * 1009,
        )[:, 0]
        cluster_se = float(np.std(boot, ddof=1))
        cluster_p = _normal_p(coefficient, cluster_se)
        raw_cluster_p[name] = cluster_p
        single_results.append(
            {
                **spec,
                "validation_status": "UNVALIDATED",
                "evidence_label": "associative_unvalidated_proxy",
                "outcome": "literal_hero",
                "timing": "strictly_before_conservative_selection_cutoff",
                "n": len(cohort),
                "present": int(signal.sum()),
                "absent": int(len(signal) - signal.sum()),
                "adjusted_risk_difference_ame": coefficient,
                "hc1_se": hc1_se,
                "hc1_ci95": [coefficient - Z_975 * hc1_se, coefficient + Z_975 * hc1_se],
                "hc1_normal_p_two_sided": _normal_p(coefficient, hc1_se),
                "two_way_cluster_bootstrap": {
                    "method": "crossed_pigeonhole_resample_model_and_scenario",
                    "seed": seed + signal_index * 1009,
                    "requested_replicates": bootstrap_reps,
                    "retained_replicates": len(boot),
                    "model_clusters": len(set(clusters_model)),
                    "scenario_clusters": len(set(clusters_scenario)),
                    "se": cluster_se,
                    "percentile_ci95": [float(np.quantile(boot, 0.025)), float(np.quantile(boot, 0.975))],
                    "normal_approximation_p_two_sided": cluster_p,
                },
                "fixed_effects": ["model_configuration", "scenario", "relative_level", "condition"],
                "design_rank": fit.rank,
                "design_columns": len(columns),
            }
        )

    adjusted = holm_adjust(raw_cluster_p)
    for item in single_results:
        item["holm_adjusted_cluster_p_six_families"] = adjusted[item["name"]]

    signal_matrix = np.column_stack(
        [np.asarray([float(bool(row["preselection"][name])) for row in cohort]) for name in FAILURE_PROXY_NAMES]
    )
    joint_x = np.column_stack((controls, signal_matrix))
    joint_columns = [*control_names, *FAILURE_PROXY_NAMES]
    joint_fit = fit_lpm(y, joint_x, joint_columns)
    coefficient_indices = list(range(len(control_names), len(joint_columns)))
    joint_boot = _cluster_bootstrap_coefficients(
        y,
        joint_x,
        joint_columns,
        clusters_model,
        clusters_scenario,
        coefficient_indices,
        reps=bootstrap_reps,
        seed=seed + 900_001,
    )
    joint_items = []
    for offset, name in enumerate(FAILURE_PROXY_NAMES):
        index = coefficient_indices[offset]
        coefficient = float(joint_fit.beta[index])
        hc1_se = float(joint_fit.se_hc1[index])
        boot = joint_boot[:, offset]
        joint_items.append(
            {
                "name": name,
                "validation_status": "UNVALIDATED",
                "evidence_label": "exploratory_joint_association_unvalidated_proxy",
                "adjusted_risk_difference_ame": coefficient,
                "hc1_se": hc1_se,
                "hc1_ci95": [coefficient - Z_975 * hc1_se, coefficient + Z_975 * hc1_se],
                "two_way_cluster_bootstrap_se": float(np.std(boot, ddof=1)),
                "two_way_cluster_bootstrap_percentile_ci95": [
                    float(np.quantile(boot, 0.025)),
                    float(np.quantile(boot, 0.975)),
                ],
            }
        )
    return {
        "schema": "agentarena.mode-proxy-associations.v1",
        "model": "linear_probability_fixed_effects",
        "interpretation": "binary-signal coefficient equals adjusted risk difference / average marginal effect",
        "cohort": "overhaul_non_absolute measured runs",
        "n": len(cohort),
        "outcome": "literal_hero",
        "single_proxy_models": single_results,
        "holm_family": list(FAILURE_PROXY_NAMES),
        "holm_input_p": "two-way-cluster-bootstrap SE with normal approximation",
        "joint_model": {
            "status": "exploratory",
            "design_rank": joint_fit.rank,
            "design_columns": len(joint_columns),
            "rank_deficient": joint_fit.rank < len(joint_columns),
            "items": joint_items,
        },
        "caveats": [
            "All six indicators are UNVALIDATED deterministic telemetry proxies until audited against blinded qualitative labels.",
            "Regex matches are observable text/action traces, not definitive latent cognitive modes.",
            "Associations are retrospective and do not identify causal component effects.",
            "The conservative selection cutoff can miss implicit choices and can stop early on ambiguous final-choice prose.",
            "Only five scenario clusters are available; crossed-cluster bootstrap intervals are unstable and descriptive-supporting rather than definitive.",
            "The transaction-drift family indicator is only absence of pre-selection safeguard intent; observed basket drift is post-selection and analyzed descriptively.",
        ],
    }


def _paired_bootstrap(values: Sequence[float], *, seed: int, reps: int) -> dict[str, Any]:
    array = np.asarray(values, dtype=float)
    if not len(array):
        return {"mean": None, "se": None, "percentile_ci95": None}
    rng = np.random.default_rng(seed)
    draws = array[rng.integers(0, len(array), size=(reps, len(array)))].mean(axis=1)
    return {
        "mean": float(array.mean()),
        "se": float(np.std(draws, ddof=1)),
        "percentile_ci95": [float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975))],
        "seed": seed,
        "replicates": reps,
    }


def _exact_mcnemar(baseline: Sequence[int], treatment: Sequence[int]) -> dict[str, Any]:
    gain = sum(a == 0 and b == 1 for a, b in zip(baseline, treatment))
    loss = sum(a == 1 and b == 0 for a, b in zip(baseline, treatment))
    discordant = gain + loss
    if discordant == 0:
        p_value = 1.0
    else:
        tail = sum(math.comb(discordant, k) for k in range(min(gain, loss) + 1)) / (2**discordant)
        p_value = min(1.0, 2 * tail)
    return {
        "baseline_0_treatment_1": gain,
        "baseline_1_treatment_0": loss,
        "discordant_pairs": discordant,
        "exact_two_sided_p": p_value,
    }


def v19_paired_analysis(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    v19 = [
        row for row in rows
        if str(row.get("cohort", "")).startswith("v19_") and row.get("analysis_included")
    ]
    grouped: defaultdict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in v19:
        grouped[str(row.get("pair_id"))].append(row)
    pairs: list[dict[str, Any]] = []
    numeric_process = (
        "candidate_id_count", "pdp_candidate_count", "visited_pdp_candidate_count",
        "search_page_count", "max_search_page_seen",
    )
    binary_process = (
        "product_api_attempt", "same_origin_fetch", "bulk_detail_fetch", "todo_or_plan_file",
        "evidence_file", "exhaustive_claim", "explicit_comparison_claim", "lexicographic_claim",
        "checkout_recheck_claim", "retrieval_failure_signal", "unknown_or_unresolved_signal",
        "repeated_action_loop", "action_error_signal",
    )
    for pair_id, members in sorted(grouped.items()):
        by_arm = {str(row.get("arm")): row for row in members}
        if set(by_arm) != {"baseline", "deliberative"} or len(members) != 2:
            raise StatisticsValidationError(f"V19 pair {pair_id!r} does not contain exactly two declared arms")
        baseline, treatment = by_arm["baseline"], by_arm["deliberative"]
        for key in ("scenario", "condition", "variant", "model", "cohort"):
            if baseline.get(key) != treatment.get(key):
                raise StatisticsValidationError(f"V19 pair {pair_id!r} disagrees on {key}")
        item: dict[str, Any] = {
            "pair_id": pair_id,
            "cohort": baseline["cohort"],
            "condition": baseline["condition"],
            "scenario": baseline["scenario"],
            "variant": baseline["variant"],
            "model": baseline["model"],
            "baseline_run_id": baseline["run_id"],
            "deliberative_run_id": treatment["run_id"],
            "outcomes": {},
            "process": {},
        }
        for key in ("literal_hero", "strict_binary", "preservation_strict"):
            a, b = float(baseline[key]), float(treatment[key])
            item["outcomes"][key] = {"baseline": a, "deliberative": b, "paired_difference": b - a}
        for key in numeric_process:
            a = float(baseline["signals"][key])
            b = float(treatment["signals"][key])
            item["process"][key] = {"baseline": a, "deliberative": b, "paired_difference": b - a}
        for key in binary_process:
            a = int(bool(baseline["signals"][key]))
            b = int(bool(treatment["signals"][key]))
            item["process"][key] = {"baseline": a, "deliberative": b, "paired_difference": b - a}
        for label, source in (
            ("decision_steps", "step_accounting"),
            ("tool_actions", "step_accounting"),
            ("seconds", None),
        ):
            a_raw = baseline[source].get(label) if source else baseline.get(label)
            b_raw = treatment[source].get(label) if source else treatment.get(label)
            if isinstance(a_raw, (int, float)) and isinstance(b_raw, (int, float)):
                a, b = float(a_raw), float(b_raw)
                item["process"][label] = {"baseline": a, "deliberative": b, "paired_difference": b - a}
        pairs.append(item)
    if len(pairs) != 30:
        raise StatisticsValidationError(f"expected exactly 30 measured V19 pairs, found {len(pairs)}")

    slices = {
        "all": pairs,
        "standard_combined": [p for p in pairs if p["cohort"] == "v19_standard" and p["condition"] == "combined"],
        "standard_clean": [p for p in pairs if p["cohort"] == "v19_standard" and p["condition"] == "clean"],
        "hard_combined": [p for p in pairs if p["cohort"] == "v19_hard" and p["condition"] == "combined"],
    }
    aggregates: dict[str, Any] = {}
    for slice_index, (name, subset) in enumerate(slices.items()):
        outcomes: dict[str, Any] = {}
        process: dict[str, Any] = {}
        for outcome_index, key in enumerate(("literal_hero", "strict_binary", "preservation_strict")):
            baseline = [p["outcomes"][key]["baseline"] for p in subset]
            treatment = [p["outcomes"][key]["deliberative"] for p in subset]
            differences = [b - a for a, b in zip(baseline, treatment)]
            outcomes[key] = {
                "baseline_sum": sum(baseline),
                "baseline_mean": sum(baseline) / len(subset),
                "deliberative_sum": sum(treatment),
                "deliberative_mean": sum(treatment) / len(subset),
                "paired_effect": _paired_bootstrap(
                    differences,
                    seed=BOOTSTRAP_SEED + 100_000 + slice_index * 101 + outcome_index,
                    reps=V19_BOOTSTRAP_REPS,
                ),
            }
            if key in {"literal_hero", "strict_binary"}:
                outcomes[key]["mcnemar"] = _exact_mcnemar(
                    [int(value) for value in baseline], [int(value) for value in treatment]
                )
        process_keys = sorted({key for pair in subset for key in pair["process"]})
        for process_index, key in enumerate(process_keys):
            baseline = [p["process"][key]["baseline"] for p in subset]
            treatment = [p["process"][key]["deliberative"] for p in subset]
            differences = [b - a for a, b in zip(baseline, treatment)]
            process[key] = {
                "baseline_mean": sum(baseline) / len(subset),
                "deliberative_mean": sum(treatment) / len(subset),
                "paired_difference": _paired_bootstrap(
                    differences,
                    seed=BOOTSTRAP_SEED + 200_000 + slice_index * 101 + process_index,
                    reps=V19_BOOTSTRAP_REPS,
                ),
            }
        aggregates[name] = {"pairs": len(subset), "outcomes": outcomes, "process": process}
    return {
        "schema": "agentarena.v19-exact-paired-statistics.v1",
        "evidence_label": "paired_whole_harness_outcomes_and_descriptive_post_treatment_process",
        "pair_count": len(pairs),
        "pairs": pairs,
        "aggregates": aggregates,
        "interpretation_boundary": (
            "Outcome differences estimate the paired whole-harness contrast. Process differences are post-treatment "
            "descriptions only; no component mediation or process-causation claim is made."
        ),
    }


def _percent(value: Any) -> str:
    return "NA" if value is None else f"{100 * float(value):.1f}%"


def _markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Quantitative trajectory-mode evidence",
        "",
        f"Frozen input: `{report['input']['sha256']}` ({report['input']['run_count']} runs).",
        "",
        "Evidence labels separate exact description, retrospective association, and the paired whole-harness contrast. "
        "Every regex-derived mode indicator is **UNVALIDATED** pending blinded qualitative-label validation.",
        "",
        "## Exact retrospective outcomes",
        "",
        "Evidence label: `exact_descriptive`.",
        "",
        "The machine-readable JSON and `outcome_cells.csv` contain the full exact model × scenario × variant × condition table. "
        "The compact marginals below are descriptive, not causal.",
        "",
        "| Model | Measured | Literal heroes | Hero rate | Strict | Mean P* |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in report["descriptive"]["by_model"]:
        lines.append(
            f"| {row['model']} | {row['measured_runs']} | {row['literal_heroes']} | "
            f"{_percent(row['literal_hero_rate'])} | {row['strict_successes']} | {row['mean_preservation_strict']:.3f} |"
        )
    lines += [
        "",
        "## Fixed-effect proxy associations",
        "",
        "Evidence label: `associative_unvalidated_proxy`.",
        "",
        "Each row is a separate LPM with model-configuration, scenario, relative-level, and condition fixed effects. "
        "The binary-signal coefficient is the adjusted hero-rate difference (AME). Holm adjustment is limited to the six frozen failure-family proxies.",
        "",
        "| UNVALIDATED proxy | Present / N | Adjusted RD | HC1 95% CI | Crossed-cluster 95% CI | Holm p |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in report["associations"]["single_proxy_models"]:
        hci = row["hc1_ci95"]
        bci = row["two_way_cluster_bootstrap"]["percentile_ci95"]
        hp = row["holm_adjusted_cluster_p_six_families"]
        lines.append(
            f"| {row['family']} | {row['present']} / {row['n']} | {row['adjusted_risk_difference_ame']:+.3f} | "
            f"[{hci[0]:+.3f}, {hci[1]:+.3f}] | [{bci[0]:+.3f}, {bci[1]:+.3f}] | "
            f"{'NA' if hp is None else f'{hp:.4g}'} |"
        )
    lines += [
        "",
        "Caveats: these are retrospective associations; five scenario clusters make crossed-cluster intervals unstable; "
        "and telemetry proxies must not be named as definitive failure modes before the label audit.",
        "",
        "## Exact paired V19 outcomes",
        "",
        "Evidence label: paired whole-harness outcomes; process measures are `descriptive_post_treatment_process`.",
        "",
        "| Slice | Pairs | Baseline heroes | Harness heroes | Hero RD | Baseline strict | Harness strict | Mean P* delta |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("standard_combined", "standard_clean", "hard_combined", "all"):
        item = report["v19"]["aggregates"][name]
        hero = item["outcomes"]["literal_hero"]
        strict = item["outcomes"]["strict_binary"]
        pstar = item["outcomes"]["preservation_strict"]
        lines.append(
            f"| {name} | {item['pairs']} | {hero['baseline_sum']:.0f} | {hero['deliberative_sum']:.0f} | "
            f"{hero['paired_effect']['mean']:+.3f} | {strict['baseline_sum']:.0f} | "
            f"{strict['deliberative_sum']:.0f} | {pstar['paired_effect']['mean']:+.3f} |"
        )
    lines += [
        "",
        "V19 process deltas are in the JSON as paired descriptive post-treatment measures. They are not mediation estimates.",
        "",
        "## Reproduce",
        "",
        "```bash",
        ".venv/bin/python scripts/analyze_mode_statistics.py",
        "```",
        "",
    ]
    return "\n".join(lines)


def build_report(
    input_path: Path, *, bootstrap_reps: int = DEFAULT_BOOTSTRAP_REPS, seed: int = BOOTSTRAP_SEED
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    raw_rows, input_manifest = load_census(input_path)
    rows = attach_preselection(raw_rows)
    overhaul = [row for row in rows if row.get("cohort") == "overhaul_non_absolute"]
    crossed = _group_outcomes(overhaul, ("model", "scenario", "variant", "condition"))
    report = {
        "schema": "agentarena.mode-statistics-report.v1",
        "input": input_manifest,
        "analysis_policy": {
            "primary_outcome": "literal_hero",
            "primary_archive_cohort": "overhaul_non_absolute",
            "infrastructure_policy": "analysis_included only; behavioral failures remain zero",
            "estimator": "linear probability model with fixed effects",
            "fixed_effects": ["model_configuration", "scenario", "relative_level", "condition"],
            "bootstrap_seed": seed,
            "bootstrap_replicates": bootstrap_reps,
            "proxy_validation_status": "UNVALIDATED",
        },
        "descriptive": {
            "evidence_label": "exact_descriptive",
            "full_cross_keys": ["model", "scenario", "variant", "condition"],
            "full_cross": crossed,
            "by_model": _group_outcomes(overhaul, ("model",)),
            "by_scenario": _group_outcomes(overhaul, ("scenario",)),
            "by_variant": _group_outcomes(overhaul, ("variant",)),
            "by_condition": _group_outcomes(overhaul, ("condition",)),
        },
        "preselection_proxy_prevalence": {
            name: sum(
                bool(row["preselection"][name])
                for row in overhaul if row.get("analysis_included")
            )
            for name in FAILURE_PROXY_NAMES
        },
        "proxy_definitions": list(FAILURE_PROXY_SPECS),
        "associations": adjusted_proxy_models(rows, bootstrap_reps=bootstrap_reps, seed=seed),
        "v19": v19_paired_analysis(rows),
    }
    return report, crossed


def write_outputs(output_dir: Path, report: Mapping[str, Any], crossed: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "mode_statistics.json"
    markdown_path = output_dir / "mode_statistics.md"
    csv_path = output_dir / "outcome_cells.csv"
    json_path.write_text(_json(report))
    markdown_path.write_text(_markdown(report))
    fields = list(crossed[0]) if crossed else []
    with csv_path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(crossed)
    artifacts = []
    for path in (json_path, markdown_path, csv_path):
        digest = _sha256(path)
        (output_dir / f"{path.name}.sha256").write_text(f"{digest}  {path.name}\n")
        artifacts.append({"path": path.name, "sha256": digest, "bytes": path.stat().st_size})
    manifest = {
        "schema": "agentarena.mode-statistics-manifest.v1",
        "input": report["input"],
        "artifacts": artifacts,
    }
    (output_dir / "manifest.json").write_text(_json(manifest))
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--bootstrap-reps", type=int, default=DEFAULT_BOOTSTRAP_REPS)
    parser.add_argument("--seed", type=int, default=BOOTSTRAP_SEED)
    args = parser.parse_args(argv)
    if args.bootstrap_reps < 100:
        parser.error("--bootstrap-reps must be at least 100")
    report, crossed = build_report(args.input.resolve(), bootstrap_reps=args.bootstrap_reps, seed=args.seed)
    manifest = write_outputs(args.output.resolve(), report, crossed)
    print(
        f"wrote {len(manifest['artifacts'])} artifacts; "
        f"archive N={report['associations']['n']}; V19 pairs={report['v19']['pair_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
