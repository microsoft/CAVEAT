#!/usr/bin/env python3
"""Deterministic mixed-method trajectory census for the harness study.

This module creates analysis-ready *observations*.  It does not ask a model to
interpret a trajectory and it never modifies a run, catalog, or production
harness.  Its primary historical cohort is the four non-absolute Amazon
relativeness levels (L1--L4) in ``results/overhaul_lb``.  L0 is audited as part
of the source matrix but is deliberately excluded from this cohort: at L0
``O=1`` and any gate-passing product has ``P*=strict_binary=1``, including
products that are not the literal fully-graded hero.

The output directory contains:

* ``runs.json`` and ``runs.csv`` -- one provenance-bound row per source run;
* ``summary.json`` -- cohort inventory and deterministic proxy prevalence;
* ``v19_contract_audit.json`` -- compiler stability/schema-concordance audit;
* SHA-256 sidecars and ``manifest.json``.

Usage::

    .venv/bin/python scripts/analyze_trajectory_modes.py
"""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import io
import json
import math
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

from _infra_classify import INFRA  # noqa: E402
import analyze_harness_improvement as prior_analysis  # noqa: E402


SCENARIOS = ("laptop", "office_chair", "mattress", "backpack", "tent")
ALL_STANDARD_VARIANTS = (
    "thresholded",
    "mixed",
    "graded",
    "graded3",
    "graded4",
)
NON_ABSOLUTE_VARIANTS = ("mixed", "graded", "graded3", "graded4")
LEVEL = {variant: index for index, variant in enumerate(ALL_STANDARD_VARIANTS)}
CONDITIONS = ("clean", "combined")
REPEATS = (1, 2, 3)
EXPECTED_OVERHAUL_MODELS = (
    "DeepSeek-V4-Flash",
    "DeepSeek-V4-Pro",
    "Kimi-K2.6",
    "Qwen3.5-122B",
    "gpt-4o",
    "gpt-5-low",
    "gpt-5-mini-low",
    "gpt-5-nano-low",
    "gpt-5.5-medium",
    "gpt-5.6-sol-high",
    "gpt-5.6-sol-low",
    "gpt-5.6-sol-medium",
    "gpt-5.6-terra-low",
    "grok-4.3",
)

DEFAULT_OVERHAUL = REPO_ROOT / "results" / "overhaul_lb"
DEFAULT_V19 = REPO_ROOT / "results" / "harness_deliberative_ab_confirmatory_v19"
DEFAULT_HARD = REPO_ROOT / "results" / "truthful_hard_v4_sol_high_n2"
DEFAULT_CATALOGS = REPO_ROOT / "benchmark_data" / "amazon"
DEFAULT_OUTPUT = REPO_ROOT / "results" / "mixed_method_trajectory_analysis"

PRODUCT_TOKEN_RE = re.compile(r"(?<![A-Z0-9-])(?:EXP-[A-Z0-9-]+|B[A-Z0-9]{9})(?![A-Z0-9-])")
PDP_RE = re.compile(r"/(?:dp|product|products)/([A-Za-z0-9_-]+)", re.I)
PAGE_RE = re.compile(r"(?:[?&]|&amp;|\\u0026)page=(\d+)", re.I)
SEARCH_RE = re.compile(r"/(?:s|search)(?:[/?#]|$)", re.I)
PRODUCT_API_RE = re.compile(r"/api/products?(?:/asin)?(?:/|\?|$)", re.I)
FILE_NAME_RE = re.compile(r"file_name['\"]?\s*:\s*['\"]([^'\"]+)['\"]", re.I)
EXHAUSTIVE_RE = re.compile(
    r"\b(?:all|every)\s+(?:the\s+)?(?:\d+\s+)?(?:reachable\s+|search\s+)?(?:result|candidate|option|product)s?\b"
    r"|\b(?:complete|full|entire|exhaustive)\s+(?:search\s+)?(?:set|catalog|market|frontier|result)s?\b"
    r"|\b(?:exhausted|exhaustion)\b",
    re.I,
)
LEXICOGRAPHIC_RE = re.compile(
    r"\b(?:primary|first)\s+(?:ranking|objective|criterion|priority)\b"
    r"|\b(?:secondary|tie[- ]?break(?:er)?)\b"
    r"|\b(?:lightest|longest|highest|lowest|cheapest)\b.{0,100}\bthen\b",
    re.I | re.S,
)
RECHECK_RE = re.compile(
    r"\b(?:cart|checkout|order review|basket)\b.{0,160}\b(?:re[- ]?check|verify|verified|exactly|identity|quantity|total)\b"
    r"|\b(?:re[- ]?check|verify|verified)\b.{0,160}\b(?:cart|checkout|order review|basket)\b",
    re.I | re.S,
)
ERROR_RE = re.compile(
    r"\b(?:validationerror|invalid (?:json|action|tool)|unsupported (?:action|navigation)|"
    r"targetclosederror|api(?:connection|timeout)error|http\s+[45]\d\d|"
    r"internal server error|action (?:failed|error)|tool (?:failed|error))\b",
    re.I,
)
RETRIEVAL_FAILURE_RE = re.compile(
    r"\b(?:fetch|extract|read|request|retrieval|product detail|pdp)\b.{0,100}"
    r"\b(?:failed|failure|error|timed out|timeout|truncated|empty|unavailable)\b"
    r"|\b(?:failed|failure|error|timed out|timeout|truncated)\b.{0,100}"
    r"\b(?:fetch|extract|read|request|retrieval|product detail|pdp)\b",
    re.I | re.S,
)
UNKNOWN_RE = re.compile(r"\b(?:unknown|unresolved|missing|conflict(?:ing)?)\b", re.I)
CART_RE = re.compile(r"\b(?:add to cart|buy now|cart|basket)\b", re.I)
CHECKOUT_RE = re.compile(r"\b(?:checkout|place (?:your )?order|order review|confirmation)\b", re.I)
COMPARE_RE = re.compile(r"\b(?:compare|comparison|rank(?:ed|ing)?|frontier|winner)\b", re.I)


class AnalysisValidationError(RuntimeError):
    """A source snapshot cannot support the declared analysis."""


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise AnalysisValidationError(f"cannot hash {_relative(path)}: {exc}") from exc
    return digest.hexdigest()


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AnalysisValidationError(f"cannot read JSON object {_relative(path)}: {exc}") from exc
    if not isinstance(value, dict):
        raise AnalysisValidationError(f"{_relative(path)} is not a JSON object")
    return value


def _canonical_json(value: Any, *, pretty: bool = False) -> str:
    if pretty:
        return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _finite_unit(value: Any, field: str, path: Path) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AnalysisValidationError(f"{_relative(path)}: {field} must be numeric")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise AnalysisValidationError(f"{_relative(path)}: {field}={value!r} is outside [0,1]")
    return result


def _is_transacted(summary: Mapping[str, Any]) -> bool:
    return bool(summary.get("chosen")) or summary.get("outcome") not in {
        None,
        "none",
        "error",
        "skipped",
    }


def _effective_scores(
    summary: Mapping[str, Any], summary_path: Path, run_dir: Path
) -> dict[str, Any]:
    transacted = _is_transacted(summary)
    raw_pstar = summary.get("preservation_strict")
    raw_strict = summary.get("strict_binary")
    if transacted:
        if raw_pstar is None or raw_strict is None:
            raise AnalysisValidationError(
                f"{_relative(summary_path)}: transacted run lacks strict score fields"
            )
        pstar = _finite_unit(raw_pstar, "preservation_strict", summary_path)
        strict = _finite_unit(raw_strict, "strict_binary", summary_path)
        if strict not in {0.0, 1.0}:
            raise AnalysisValidationError(
                f"{_relative(summary_path)}: strict_binary must be 0 or 1"
            )
        score_source = "strict_summary_fields"
    else:
        if raw_pstar is not None and _finite_unit(raw_pstar, "preservation_strict", summary_path) != 0:
            raise AnalysisValidationError(f"{_relative(summary_path)}: no-order P* is nonzero")
        if raw_strict is not None and _finite_unit(raw_strict, "strict_binary", summary_path) != 0:
            raise AnalysisValidationError(f"{_relative(summary_path)}: no-order strict score is nonzero")
        pstar = strict = 0.0
        score_source = "behavioral_no_order_zero"

    matched_path = _relative(run_dir)
    override = prior_analysis.FORCED_ZERO_RUNS.get(matched_path)
    if override is None:
        return {
            "outcome": summary.get("outcome"),
            "chosen": summary.get("chosen"),
            "chosen_label": summary.get("chosen_label"),
            "pstar": pstar,
            "strict_binary": strict,
            "score_source": score_source,
            "scoring_override": None,
            "raw_score_and_selection": None,
        }
    return {
        "outcome": "none",
        "chosen": None,
        "chosen_label": None,
        "pstar": 0.0,
        "strict_binary": 0.0,
        "score_source": "path_scoped_user_directed_zero",
        "scoring_override": {**override, "matched_path": matched_path},
        "raw_score_and_selection": {
            "outcome": summary.get("outcome"),
            "chosen": summary.get("chosen"),
            "chosen_label": summary.get("chosen_label"),
            "pstar": pstar,
            "strict_binary": strict,
            "score_source": score_source,
        },
    }


def _parse_task(task_id: str) -> dict[str, Any]:
    if not isinstance(task_id, str) or "-" not in task_id:
        raise AnalysisValidationError(f"malformed task_id {task_id!r}")
    task_scenario, variant = task_id.rsplit("-", 1)
    if variant not in ALL_STANDARD_VARIANTS:
        raise AnalysisValidationError(f"unsupported task variant in {task_id!r}")
    if task_scenario.endswith("_steerhard_v4"):
        base = task_scenario[: -len("_steerhard_v4")]
        catalog_scenario = f"{base}_hard"
        hard = True
    elif task_scenario.endswith("_hard"):
        base = task_scenario[: -len("_hard")]
        catalog_scenario = task_scenario
        hard = True
    else:
        base = task_scenario
        catalog_scenario = task_scenario
        hard = False
    if base not in SCENARIOS:
        raise AnalysisValidationError(f"unsupported scenario in {task_id!r}")
    return {
        "scenario": base,
        "catalog_scenario": catalog_scenario,
        "variant": variant,
        "relative_level": LEVEL[variant],
        "hard_catalog": hard,
    }


def _load_catalog(catalog_root: Path, catalog_scenario: str) -> dict[str, Any]:
    pool_path = catalog_root / catalog_scenario / "pool.json"
    try:
        pool = json.loads(pool_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise AnalysisValidationError(f"cannot load {_relative(pool_path)}: {exc}") from exc
    if not isinstance(pool, list) or not pool:
        raise AnalysisValidationError(f"{_relative(pool_path)} has no product list")
    ids = [row.get("asin") for row in pool if isinstance(row, dict)]
    if any(not isinstance(item, str) or not item for item in ids) or len(ids) != len(set(ids)):
        raise AnalysisValidationError(f"{_relative(pool_path)} has missing/duplicate product IDs")
    heroes = [
        row["asin"]
        for row in pool
        if isinstance(row, dict) and row.get("decoy_kind") == "hero"
    ]
    if len(heroes) != 1:
        raise AnalysisValidationError(
            f"{_relative(pool_path)}: expected one literal hero, found {heroes}"
        )
    return {
        "catalog_scenario": catalog_scenario,
        "ids": frozenset(ids),
        "hero": heroes[0],
        "pool_path": pool_path,
        "pool_sha256": _sha256(pool_path),
    }


def _catalog_cache(catalog_root: Path) -> dict[str, dict[str, Any]]:
    return {
        name: _load_catalog(catalog_root, name)
        for name in (*SCENARIOS, *(f"{scenario}_hard" for scenario in SCENARIOS))
    }


def _action_signature(action: str) -> str:
    """Normalize serialization only; retain action parameters and their order."""
    parsed: Any = None
    try:
        parsed = json.loads(action)
    except (json.JSONDecodeError, TypeError):
        try:
            parsed = ast.literal_eval(action)
        except (ValueError, SyntaxError):
            return re.sub(r"\s+", " ", action).strip()

    def strip_rendered(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): strip_rendered(item)
                for key, item in value.items()
                if key != "interacted_element"
            }
        if isinstance(value, (list, tuple)):
            return [strip_rendered(item) for item in value]
        return value

    try:
        return _canonical_json(strip_rendered(parsed))
    except (TypeError, ValueError):
        return re.sub(r"\s+", " ", action).strip()


def _ordered_catalog_ids(text: str, catalog_ids: frozenset[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for match in PRODUCT_TOKEN_RE.finditer(text.upper()):
        product_id = match.group(0)
        if product_id in catalog_ids and product_id not in seen:
            seen.add(product_id)
            ordered.append(product_id)
    return ordered


def _evidence_indices(steps: Sequence[Mapping[str, Any]], pattern: re.Pattern[str]) -> list[int]:
    result: list[int] = []
    for position, step in enumerate(steps, 1):
        text = "\n".join(
            str(step.get(key) or "") for key in ("action", "reasoning", "note", "url")
        )
        if pattern.search(text):
            raw_index = step.get("index")
            result.append(raw_index if isinstance(raw_index, int) else position)
    return result


def extract_signals(
    trajectory: Mapping[str, Any], catalog_ids: frozenset[str]
) -> dict[str, Any]:
    steps = [step for step in (trajectory.get("steps") or []) if isinstance(step, dict)]
    actions = [str(step.get("action") or "") for step in steps]
    reasoning = [str(step.get("reasoning") or "") for step in steps]
    notes = [str(step.get("note") or "") for step in steps]
    urls = [str(step.get("url") or "") for step in steps]
    answer = str(trajectory.get("answer") or "")
    blob = "\n".join((*actions, *reasoning, *notes, *urls, answer))
    action_blob = "\n".join(actions)
    reasoning_blob = "\n".join(reasoning)
    url_blob = "\n".join(urls)
    lower = blob.lower()

    candidates = _ordered_catalog_ids(blob, catalog_ids)
    pdp_candidates = sorted(
        {
            product_id.upper()
            for product_id in PDP_RE.findall(blob)
            if product_id.upper() in catalog_ids
        }
    )
    visited_pdp_candidates = sorted(
        {
            product_id.upper()
            for product_id in PDP_RE.findall(url_blob)
            if product_id.upper() in catalog_ids
        }
    )
    pages = {int(value) for value in PAGE_RE.findall(blob)}
    if SEARCH_RE.search(blob):
        pages.add(1)

    file_names = sorted(set(FILE_NAME_RE.findall(action_blob)))
    todo_files = [
        name
        for name in file_names
        if Path(name).name.lower() in {"todo.md", "plan.md", "task.md"}
    ]
    evidence_files = [name for name in file_names if name not in todo_files]
    same_origin_fetch = "fetch(" in lower
    api_attempt = bool(PRODUCT_API_RE.search(blob))
    bulk_fetch = same_origin_fetch and bool(
        re.search(r"promise\.all|queryselectorall|for\s*\(|\.map\s*\(|batch|bulk", blob, re.I)
        or len(pdp_candidates) >= 2
    )

    signatures = [_action_signature(action) for action in actions if action.strip()]
    signature_counts = Counter(signatures)
    longest_consecutive = 0
    current = 0
    previous: str | None = None
    for signature in signatures:
        if signature == previous:
            current += 1
        else:
            previous = signature
            current = 1
        longest_consecutive = max(longest_consecutive, current)
    most_repeated = max(signature_counts.values(), default=0)
    repeated_loop = longest_consecutive >= 3 or most_repeated >= 5

    exhaustive_steps = _evidence_indices(steps, EXHAUSTIVE_RE)
    lexicographic_steps = _evidence_indices(steps, LEXICOGRAPHIC_RE)
    recheck_steps = _evidence_indices(steps, RECHECK_RE)
    error_steps = _evidence_indices(steps, ERROR_RE)
    retrieval_failure_steps = _evidence_indices(steps, RETRIEVAL_FAILURE_RE)
    unknown_steps = _evidence_indices(steps, UNKNOWN_RE)
    cart_steps = _evidence_indices(steps, CART_RE)
    checkout_steps = _evidence_indices(steps, CHECKOUT_RE)
    comparison_steps = _evidence_indices(steps, COMPARE_RE)

    return {
        "candidate_ids": candidates,
        "candidate_id_count": len(candidates),
        "pdp_candidate_ids": pdp_candidates,
        "pdp_candidate_count": len(pdp_candidates),
        "visited_pdp_candidate_ids": visited_pdp_candidates,
        "visited_pdp_candidate_count": len(visited_pdp_candidates),
        "search_pages_seen": sorted(pages),
        "search_page_count": len(pages),
        "max_search_page_seen": max(pages) if pages else 0,
        "product_api_attempt": api_attempt,
        "same_origin_fetch": same_origin_fetch,
        "bulk_detail_fetch": bulk_fetch,
        "todo_or_plan_file": bool(todo_files),
        "todo_or_plan_files": todo_files,
        "evidence_file": bool(evidence_files),
        "evidence_files": evidence_files,
        "exhaustive_claim": bool(exhaustive_steps),
        "exhaustive_claim_steps": exhaustive_steps,
        "lexicographic_claim": bool(lexicographic_steps),
        "lexicographic_claim_steps": lexicographic_steps,
        "checkout_recheck_claim": bool(recheck_steps),
        "checkout_recheck_steps": recheck_steps,
        "explicit_comparison_claim": bool(comparison_steps),
        "explicit_comparison_steps": comparison_steps,
        "action_error_signal": bool(error_steps),
        "action_error_steps": error_steps,
        "retrieval_failure_signal": bool(retrieval_failure_steps),
        "retrieval_failure_steps": retrieval_failure_steps,
        "unknown_or_unresolved_signal": bool(unknown_steps),
        "unknown_or_unresolved_steps": unknown_steps,
        "repeated_action_loop": repeated_loop,
        "longest_consecutive_action_repeat": longest_consecutive,
        "max_action_signature_frequency": most_repeated,
        "cart_action_steps": cart_steps,
        "checkout_action_steps": checkout_steps,
    }


def _basket(trajectory: Mapping[str, Any]) -> dict[str, Any]:
    evaluation = trajectory.get("evaluation") or {}
    details = evaluation.get("details") or {}
    basket = details.get("basket") or {}
    line_items = basket.get("line_items") or []
    if not isinstance(line_items, list):
        line_items = []
    add_ons = [
        item
        for item in line_items
        if isinstance(item, dict)
        and (
            str(item.get("asin") or "").startswith("ADDON-")
            or re.search(
                r"\b(?:protection|subscription|membership|installation)\b",
                str(item.get("title") or ""),
                re.I,
            )
        )
    ]
    violations = [str(item) for item in (details.get("violations") or [])]
    quantities = [
        int(item.get("quantity") or 0)
        for item in line_items
        if isinstance(item, dict) and isinstance(item.get("quantity"), (int, float))
    ]
    return {
        "evaluation_role": details.get("role"),
        "violations": violations,
        "hard_constraint_violation": bool(violations),
        "line_item_count": len(line_items),
        "total_quantity": sum(quantities),
        "extra_line_items": max(0, len(line_items) - 1),
        "add_on_count": len(add_ons),
        "service_fee": float(basket.get("service_fee") or 0.0),
        "subscription_count": int(basket.get("subscription_count") or 0),
        "all_in_total": basket.get("all_in_total"),
        "subtotal": basket.get("subtotal"),
    }


def _step_accounting(summary: Mapping[str, Any], trajectory: Mapping[str, Any]) -> dict[str, Any]:
    stats = trajectory.get("stats") or {}
    summary_steps = summary.get("num_steps")
    if not isinstance(summary_steps, int) or summary_steps < 0:
        raise AnalysisValidationError("summary num_steps must be a nonnegative integer")
    decision = stats.get("decision_steps")
    tools = stats.get("tool_actions")
    if isinstance(decision, int) and isinstance(tools, int):
        return {
            "semantics": "decision_steps_and_tool_actions",
            "summary_num_steps": summary_steps,
            "decision_steps": decision,
            "tool_actions": tools,
            "legacy_flattened_actions": None,
        }
    return {
        "semantics": "historical_flattened_atomic_tool_actions",
        "summary_num_steps": summary_steps,
        "decision_steps": None,
        "tool_actions": None,
        "legacy_flattened_actions": summary_steps,
    }


def _decompose_standard(
    trajectory_path: Path,
    *,
    pstar: float,
    transacted: bool,
    variant: str,
) -> dict[str, Any]:
    if not transacted:
        return {"G": None, "O": None, "source": "not_identifiable_without_purchase"}
    try:
        from agentarena.scoring.continuous import STRICT_GAMMA, _field_of
        from agentarena.scoring.rescore import _cell_criteria, _must_haves

        criteria = _cell_criteria(str(trajectory_path))
    except Exception as exc:  # fail closed below; include cause for diagnosis
        raise AnalysisValidationError(
            f"cannot reconstruct G/O for {_relative(trajectory_path)}: {type(exc).__name__}: {exc}"
        ) from exc
    if criteria is None:
        return {"G": None, "O": None, "source": "catalog_reconstruction_unavailable"}
    if criteria == "off":
        return {"G": 0.0, "O": None, "source": "off_catalog_gate_zero"}
    cs, scenario, resolved_variant = criteria
    if resolved_variant != variant:
        raise AnalysisValidationError(
            f"{_relative(trajectory_path)}: reconstructed variant {resolved_variant} != {variant}"
        )
    hard_fields = set(_must_haves(scenario, variant))
    gate = 1.0
    terms: list[float] = []
    for key, criterion in cs.per_criterion.items():
        if _field_of(key) in hard_fields:
            if criterion["s"] < 1.0:
                gate = 0.0
        else:
            terms.append(float(criterion["s"]) ** STRICT_GAMMA)
    optimality = statistics.mean(terms) if terms else 1.0
    reconstructed = gate * optimality
    if not math.isclose(reconstructed, pstar, rel_tol=0.0, abs_tol=5.1e-5):
        raise AnalysisValidationError(
            f"{_relative(trajectory_path)}: reconstructed G*O={reconstructed:.8f} "
            f"disagrees with stored P*={pstar:.8f}"
        )
    return {
        "G": gate,
        "O": optimality,
        "source": "recomputed_current_catalog_criteria",
    }


def _decompose_nonstandard(
    *, pstar: float, transacted: bool, violations: Sequence[str]
) -> dict[str, Any]:
    if not transacted:
        return {"G": None, "O": None, "source": "not_identifiable_without_purchase"}
    if pstar > 0:
        return {"G": 1.0, "O": pstar, "source": "algebraic_positive_pstar"}
    if violations:
        return {"G": 0.0, "O": None, "source": "evaluator_violation_gate_zero"}
    return {"G": None, "O": None, "source": "zero_product_factors_not_identifiable"}


def _build_record(
    run_dir: Path,
    *,
    cohort: str,
    source_campaign: str,
    catalog: Mapping[str, Any],
    infra: Mapping[str, Any] | None = None,
    storefront_audit: Mapping[str, Any] | None = None,
    pair_id: str | None = None,
    arm: str | None = None,
) -> dict[str, Any]:
    summary_path = run_dir / "summary.json"
    trajectory_path = run_dir / "trajectory.json"
    summary = _load_object(summary_path)
    trajectory = _load_object(trajectory_path)
    if summary.get("env") != "amazon":
        raise AnalysisValidationError(f"{_relative(summary_path)}: environment is not amazon")
    task = _parse_task(str(summary.get("task_id") or ""))
    if task["catalog_scenario"] != catalog["catalog_scenario"]:
        raise AnalysisValidationError(f"{_relative(summary_path)}: catalog/task mismatch")
    for key in ("model", "scaffold", "condition"):
        if summary.get(key) != trajectory.get(key):
            raise AnalysisValidationError(f"{_relative(run_dir)}: {key} differs across artifacts")
    if summary.get("task_id") != trajectory.get("task_id"):
        raise AnalysisValidationError(f"{_relative(run_dir)}: task_id differs across artifacts")

    scores = _effective_scores(summary, summary_path, run_dir)
    signals = extract_signals(trajectory, catalog["ids"])
    basket = _basket(trajectory)
    transacted = scores["chosen"] is not None and scores["outcome"] not in {
        None,
        "none",
        "error",
        "skipped",
    }
    if task["hard_catalog"]:
        decomposition = _decompose_nonstandard(
            pstar=scores["pstar"], transacted=transacted, violations=basket["violations"]
        )
    else:
        decomposition = _decompose_standard(
            trajectory_path,
            pstar=scores["pstar"],
            transacted=transacted,
            variant=task["variant"],
        )
    literal_hero = int(scores["chosen"] == catalog["hero"])
    if scores["chosen"] is None:
        selected_role = "no_order"
    elif literal_hero:
        selected_role = "literal_hero"
    else:
        selected_role = "nonhero"

    included = infra is None or infra.get("class") != INFRA
    return {
        "schema": "agentarena.mixed-method-run.v1",
        "cohort": cohort,
        "source_campaign": source_campaign,
        "run_id": _relative(run_dir),
        "pair_id": pair_id,
        "arm": arm,
        "model": summary.get("model"),
        "scaffold": summary.get("scaffold"),
        "task_id": summary.get("task_id"),
        **task,
        "condition": summary.get("condition"),
        "outcome": scores["outcome"],
        "chosen": scores["chosen"],
        "chosen_label": scores["chosen_label"],
        "literal_hero_asin": catalog["hero"],
        "literal_hero": literal_hero,
        "selected_role": selected_role,
        "preservation_strict": scores["pstar"],
        "strict_binary": scores["strict_binary"],
        "G": decomposition["G"],
        "O": decomposition["O"],
        "decomposition_source": decomposition["source"],
        "score_source": scores["score_source"],
        "scoring_override": scores["scoring_override"],
        "raw_score_and_selection": scores["raw_score_and_selection"],
        "analysis_included": included,
        "infra": dict(infra or {"class": "not_applicable", "code": "not_classified"}),
        "storefront_cart_audit": dict(storefront_audit or {}),
        "step_accounting": _step_accounting(summary, trajectory),
        "seconds": float(summary.get("seconds") or (trajectory.get("stats") or {}).get("seconds") or 0.0),
        "signals": signals,
        "basket": basket,
        "provenance": {
            "summary_path": _relative(summary_path),
            "summary_sha256": _sha256(summary_path),
            "trajectory_path": _relative(trajectory_path),
            "trajectory_sha256": _sha256(trajectory_path),
            "catalog_pool_path": _relative(catalog["pool_path"]),
            "catalog_pool_sha256": catalog["pool_sha256"],
        },
    }


def scan_overhaul(
    root: Path, catalogs: Mapping[str, Mapping[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    summary_paths = sorted(root.glob("overhaul_lb_r*/amazon__browseruse__*/summary.json"))
    full_seen: set[tuple[int, str, str, str]] = set()
    selected: list[tuple[Path, int, dict[str, Any]]] = []
    for summary_path in summary_paths:
        summary = _load_object(summary_path)
        round_match = re.fullmatch(r"overhaul_lb_r([123])", summary_path.parent.parent.name)
        if round_match is None:
            raise AnalysisValidationError(f"bad overhaul round path: {_relative(summary_path)}")
        repeat = int(round_match.group(1))
        task = _parse_task(str(summary.get("task_id") or ""))
        if task["hard_catalog"]:
            raise AnalysisValidationError(f"hard task leaked into overhaul: {_relative(summary_path)}")
        if summary.get("condition") not in CONDITIONS:
            raise AnalysisValidationError(f"bad condition in {_relative(summary_path)}")
        if summary.get("model") not in EXPECTED_OVERHAUL_MODELS:
            raise AnalysisValidationError(f"unexpected model in {_relative(summary_path)}")
        if summary.get("scaffold") != "browseruse":
            raise AnalysisValidationError(f"unexpected scaffold in {_relative(summary_path)}")
        key = (repeat, str(summary["model"]), str(summary["task_id"]), str(summary["condition"]))
        if key in full_seen:
            raise AnalysisValidationError(f"duplicate overhaul cell {key}")
        full_seen.add(key)
        if task["variant"] in NON_ABSOLUTE_VARIANTS:
            selected.append((summary_path.parent, repeat, task))

    expected = {
        (repeat, model, f"{scenario}-{variant}", condition)
        for repeat in REPEATS
        for model in EXPECTED_OVERHAUL_MODELS
        for scenario in SCENARIOS
        for variant in ALL_STANDARD_VARIANTS
        for condition in CONDITIONS
    }
    if full_seen != expected:
        missing = sorted(expected - full_seen)[:10]
        extra = sorted(full_seen - expected)[:10]
        raise AnalysisValidationError(
            f"overhaul matrix mismatch: seen={len(full_seen)} expected={len(expected)} "
            f"missing={missing} extra={extra}"
        )

    records: list[dict[str, Any]] = []
    for run_dir, repeat, task in selected:
        summary = _load_object(run_dir / "summary.json")
        trajectory = _load_object(run_dir / "trajectory.json")
        infra, cart_audit = prior_analysis._classify_with_storefront_audit(
            run_dir, summary, trajectory
        )
        record = _build_record(
            run_dir,
            cohort="overhaul_non_absolute",
            source_campaign="overhaul_lb",
            catalog=catalogs[task["catalog_scenario"]],
            infra=infra,
            storefront_audit=cart_audit,
        )
        record["repeat"] = repeat
        records.append(record)

    records.sort(key=lambda row: row["run_id"])
    measured = sum(row["analysis_included"] for row in records)
    inventory = {
        "full_matrix_cells": len(full_seen),
        "full_matrix_expected": len(expected),
        "l0_absolute_cells_audited_then_excluded": len(full_seen) - len(records),
        "non_absolute_inventory_cells": len(records),
        "non_absolute_infra_excluded": len(records) - measured,
        "non_absolute_measured_cells": measured,
        "infra_codes": dict(
            sorted(
                Counter(
                    row["infra"].get("code")
                    for row in records
                    if not row["analysis_included"]
                ).items()
            )
        ),
    }
    if inventory != {
        "full_matrix_cells": 2100,
        "full_matrix_expected": 2100,
        "l0_absolute_cells_audited_then_excluded": 420,
        "non_absolute_inventory_cells": 1680,
        "non_absolute_infra_excluded": 3,
        "non_absolute_measured_cells": 1677,
        "infra_codes": {"endpoint_abort": 2, "storefront_cart_http_500": 1},
    }:
        raise AnalysisValidationError(f"overhaul frozen inventory drifted: {inventory}")
    return records, inventory


def _v19_path_metadata(path: Path) -> dict[str, Any]:
    experiment = path.parent.parent.name
    match = re.search(
        r"_b(?P<block>\d+)_r(?P<repeat>\d+)_(?P<scenario>.+)_(?P<arm>baseline|deliberative)$",
        experiment,
    )
    if match is None:
        raise AnalysisValidationError(f"cannot parse V19 experiment {experiment!r}")
    return {
        "block": int(match.group("block")),
        "repeat": int(match.group("repeat")),
        "experiment_scenario": match.group("scenario"),
        "arm": match.group("arm"),
    }


def scan_v19(
    root: Path, catalogs: Mapping[str, Mapping[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    run_root = root / "runs" if (root / "runs").is_dir() else root
    summary_paths = sorted(run_root.rglob("summary.json"))
    if len(summary_paths) != 60:
        raise AnalysisValidationError(f"V19 expected 60 summaries, found {len(summary_paths)}")
    records: list[dict[str, Any]] = []
    pairs: defaultdict[tuple[Any, ...], set[str]] = defaultdict(set)
    for summary_path in summary_paths:
        summary = _load_object(summary_path)
        metadata = _v19_path_metadata(summary_path)
        task = _parse_task(str(summary.get("task_id") or ""))
        if task["variant"] != "graded" or summary.get("condition") not in CONDITIONS:
            raise AnalysisValidationError(f"V19 protocol mismatch: {_relative(summary_path)}")
        if task["catalog_scenario"] != metadata["experiment_scenario"]:
            raise AnalysisValidationError(f"V19 path/task mismatch: {_relative(summary_path)}")
        expected_scaffold = (
            "browseruse-deliberative" if metadata["arm"] == "deliberative" else "browseruse"
        )
        if summary.get("scaffold") != expected_scaffold:
            raise AnalysisValidationError(f"V19 arm/scaffold mismatch: {_relative(summary_path)}")
        trajectory = _load_object(summary_path.parent / "trajectory.json")
        infra, cart_audit = prior_analysis._classify_with_storefront_audit(
            summary_path.parent, summary, trajectory
        )
        if infra.get("class") == INFRA:
            raise AnalysisValidationError(f"V19 contains infra-invalid run: {_relative(summary_path)}")
        pair_key = (
            metadata["block"],
            metadata["repeat"],
            summary.get("task_id"),
            summary.get("model"),
            summary.get("condition"),
        )
        if metadata["arm"] in pairs[pair_key]:
            raise AnalysisValidationError(f"duplicate V19 pair arm: {pair_key}")
        pairs[pair_key].add(metadata["arm"])
        pair_id = "|".join(str(item) for item in pair_key)
        record = _build_record(
            summary_path.parent,
            cohort="v19_hard" if task["hard_catalog"] else "v19_standard",
            source_campaign="harness_deliberative_ab_confirmatory_v19",
            catalog=catalogs[task["catalog_scenario"]],
            infra=infra,
            storefront_audit=cart_audit,
            pair_id=pair_id,
            arm=metadata["arm"],
        )
        record.update({"block": metadata["block"], "repeat": metadata["repeat"]})
        records.append(record)
    incomplete = [key for key, arms in pairs.items() if arms != {"baseline", "deliberative"}]
    if len(pairs) != 30 or incomplete:
        raise AnalysisValidationError(f"V19 pairing mismatch: pairs={len(pairs)} incomplete={incomplete}")
    records.sort(key=lambda row: row["run_id"])
    return records, {
        "inventory_cells": len(records),
        "paired_blocks": len(pairs),
        "analysis_included": sum(row["analysis_included"] for row in records),
        "standard_cells": sum(row["cohort"] == "v19_standard" for row in records),
        "hard_cells": sum(row["cohort"] == "v19_hard" for row in records),
        "baseline_cells": sum(row["arm"] == "baseline" for row in records),
        "deliberative_cells": sum(row["arm"] == "deliberative" for row in records),
    }


def scan_canonical_hard(
    root: Path, catalogs: Mapping[str, Mapping[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    run_root = root / "runs" if (root / "runs").is_dir() else root
    summary_paths = sorted(run_root.rglob("summary.json"))
    if len(summary_paths) != 10:
        raise AnalysisValidationError(f"canonical hard expected 10 summaries, found {len(summary_paths)}")
    records: list[dict[str, Any]] = []
    scenario_counts: Counter[str] = Counter()
    for summary_path in summary_paths:
        summary = _load_object(summary_path)
        task = _parse_task(str(summary.get("task_id") or ""))
        if not task["hard_catalog"] or task["variant"] != "graded":
            raise AnalysisValidationError(f"canonical hard protocol mismatch: {_relative(summary_path)}")
        trajectory = _load_object(summary_path.parent / "trajectory.json")
        infra, cart_audit = prior_analysis._classify_with_storefront_audit(
            summary_path.parent, summary, trajectory
        )
        if infra.get("class") == INFRA:
            raise AnalysisValidationError(
                f"canonical hard contains infra-invalid run: {_relative(summary_path)}"
            )
        scenario_counts[task["scenario"]] += 1
        records.append(
            _build_record(
                summary_path.parent,
                cohort="canonical_hard",
                source_campaign="truthful_hard_v4_sol_high_n2",
                catalog=catalogs[task["catalog_scenario"]],
                infra=infra,
                storefront_audit=cart_audit,
            )
        )
    if scenario_counts != Counter({scenario: 2 for scenario in SCENARIOS}):
        raise AnalysisValidationError(f"canonical hard scenario matrix drifted: {scenario_counts}")
    records.sort(key=lambda row: row["run_id"])
    return records, {
        "inventory_cells": len(records),
        "analysis_included": sum(row["analysis_included"] for row in records),
        "by_scenario": dict(sorted(scenario_counts.items())),
    }


CONTRACT_ALIASES: dict[str, dict[str, str]] = {
    "laptop": {
        "price": "price", "ssd_storage": "storage_gb", "storage": "storage_gb",
        "customer_rating": "rating", "screen_brightness": "brightness_nits",
        "laptop_type": "gaming", "gaming_laptop": "gaming", "weight": "weight_kg",
        "battery_life": "battery_hours",
    },
    "office_chair": {
        "price": "price", "weight_capacity": "weight_capacity_lbs",
        "customer_rating": "rating", "seat_cushion_thickness": "cushion_mm",
        "adjustable_lumbar_support": "adjustable_lumbar",
        "warranty_length": "warranty_years", "warranty_duration": "warranty_years",
        "recline_angle": "recline_degrees", "recline_extent": "recline_degrees",
    },
    "mattress": {
        "price": "price", "thickness": "thickness_in", "customer_rating": "rating",
        "foam_density": "foam_density_kg", "certipur_us_certification": "certipur_certified",
        "certipur_us_certified": "certipur_certified", "certipur_us": "certipur_certified",
        "foam_certification": "certipur_certified", "mattress_size": "mattress_size",
        "size": "mattress_size", "sleep_trial_duration": "trial_nights",
        "sleep_trial": "trial_nights", "warranty_duration": "warranty_years",
        "warranty": "warranty_years", "durability": "context:durability",
        "support": "context:support",
    },
    "backpack": {
        "price": "price", "capacity": "capacity_liters", "customer_rating": "rating",
        "water_resistance": "water_resist_mm", "water_resistance_rating": "water_resist_mm",
        "padded_laptop_sleeve": "has_laptop_sleeve", "weight": "weight_kg",
        "warranty_length": "warranty_years", "warranty_duration": "warranty_years",
        "warranty": "warranty_years",
    },
    "tent": {
        "price": "price", "sleeping_capacity": "capacity_person", "customer_rating": "rating",
        "waterproof_rating": "waterproof_mm", "rainfly_coverage": "has_full_rainfly",
        "full_coverage_rainfly": "has_full_rainfly", "weight": "weight_kg",
        "warranty_duration": "warranty_years", "warranty_length": "warranty_years",
        "warranty": "warranty_years", "product_type": "context:product_type",
    },
}


def _reference_contract(catalog_root: Path, task: Mapping[str, Any]) -> dict[str, Any]:
    preferences_path = catalog_root / task["catalog_scenario"] / "preferences.json"
    instructions_path = catalog_root / task["catalog_scenario"] / "instructions.json"
    preferences = _load_object(preferences_path)
    instructions = _load_object(instructions_path)
    preference = preferences.get(task["variant"])
    instruction = instructions.get(task["variant"])
    if not isinstance(preference, dict) or not isinstance(instruction, dict):
        raise AnalysisValidationError(f"missing reference task in {_relative(preferences_path)}")

    constraints: dict[str, dict[str, Any]] = {}
    for row in preference.get("thresholds") or []:
        key = str(row["key"])
        if "__" in key:
            field, raw_operator = key.rsplit("__", 1)
        else:
            field, raw_operator = key, "eq"
        operator = {
            "min": "ge", "ge": "ge", "gt": "gt", "max": "le", "le": "le", "lt": "lt"
        }.get(raw_operator, raw_operator)
        constraints[field] = {"operator": operator, "expected": row.get("value")}
    objectives = {
        str(row["attr"]): {
            "direction": "minimize" if row.get("direction") == "lower" else "maximize"
        }
        for row in (preference.get("graded") or [])
    }
    return {
        "constraints": constraints,
        "objectives": objectives,
        "instruction": instruction.get("text"),
        "preferences_path": preferences_path,
        "preferences_sha256": _sha256(preferences_path),
        "instructions_path": instructions_path,
        "instructions_sha256": _sha256(instructions_path),
    }


def _contract_fingerprint(raw: str) -> str:
    encoded = raw.encode("utf-8")
    return hashlib.sha256(len(encoded).to_bytes(8, "big") + encoded).hexdigest()


def _normal_value(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip().casefold()
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _audit_contract_row(
    trajectory_path: Path, catalog_root: Path
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    trajectory = _load_object(trajectory_path)
    task = _parse_task(str(trajectory.get("task_id") or ""))
    diagnostics = (trajectory.get("stats") or {}).get("deliberative")
    if not isinstance(diagnostics, dict):
        raise AnalysisValidationError(f"{_relative(trajectory_path)} lacks deliberative diagnostics")
    raw = diagnostics.get("contract_canonical_json")
    if not isinstance(raw, str) or not raw:
        raise AnalysisValidationError(f"{_relative(trajectory_path)} lacks canonical contract")
    try:
        contract = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AnalysisValidationError(f"{_relative(trajectory_path)} contract is not JSON") from exc
    if raw != _canonical_json(contract):
        raise AnalysisValidationError(f"{_relative(trajectory_path)} contract is not canonical")
    fingerprint = _contract_fingerprint(raw)
    if fingerprint != diagnostics.get("contract_sha256"):
        raise AnalysisValidationError(f"{_relative(trajectory_path)} contract hash mismatch")
    constraints = contract.get("constraints")
    objectives = contract.get("objectives")
    if (
        not isinstance(constraints, list)
        or not isinstance(objectives, list)
        or len(constraints) != diagnostics.get("constraint_count")
        or len(objectives) != diagnostics.get("objective_count")
    ):
        raise AnalysisValidationError(f"{_relative(trajectory_path)} contract count mismatch")

    reference = _reference_contract(catalog_root, task)
    aliases = CONTRACT_ALIASES[task["scenario"]]
    compiled_constraints: dict[str, dict[str, Any]] = {}
    compiled_objectives: dict[str, dict[str, Any]] = {}
    extras: list[dict[str, str]] = []
    for kind, rows, destination in (
        ("constraint", constraints, compiled_constraints),
        ("objective", objectives, compiled_objectives),
    ):
        for item in rows:
            criterion_id = str(item.get("criterion_id") or "")
            mapped = aliases.get(criterion_id)
            if mapped is None or mapped.startswith("context:"):
                extras.append(
                    {
                        "kind": kind,
                        "criterion_id": criterion_id,
                        "classification": mapped or "unmapped",
                    }
                )
                continue
            destination[mapped] = item

    reference_constraints = reference["constraints"]
    reference_objectives = reference["objectives"]
    constraint_missing = sorted(set(reference_constraints) - set(compiled_constraints))
    objective_missing = sorted(set(reference_objectives) - set(compiled_objectives))
    constraint_checks: dict[str, dict[str, Any]] = {}
    for field in sorted(set(reference_constraints) & set(compiled_constraints)):
        observed = compiled_constraints[field]
        expected = reference_constraints[field]
        constraint_checks[field] = {
            "operator_match": observed.get("operator") == expected["operator"],
            "expected_value_match": _normal_value(observed.get("expected"))
            == _normal_value(expected["expected"]),
            "observed_operator": observed.get("operator"),
            "reference_operator": expected["operator"],
            "observed_expected": observed.get("expected"),
            "reference_expected": expected["expected"],
        }
    objective_checks: dict[str, dict[str, Any]] = {}
    for field in sorted(set(reference_objectives) & set(compiled_objectives)):
        observed = compiled_objectives[field]
        expected = reference_objectives[field]
        objective_checks[field] = {
            "direction_match": observed.get("direction") == expected["direction"],
            "observed_direction": observed.get("direction"),
            "reference_direction": expected["direction"],
        }

    priorities = [item.get("priority") for item in objectives]
    weights = [item.get("weight") for item in objectives]
    semantic_payload = {
        "constraints": sorted(
            (
                aliases.get(str(item.get("criterion_id") or ""), f"unmapped:{item.get('criterion_id')}"),
                item.get("operator"),
                _normal_value(item.get("expected")),
            )
            for item in constraints
        ),
        "objectives": [
            (
                aliases.get(str(item.get("criterion_id") or ""), f"unmapped:{item.get('criterion_id')}"),
                item.get("direction"),
                item.get("priority"),
                item.get("weight"),
            )
            for item in objectives
        ],
        "search_mode": contract.get("search_mode"),
    }
    semantic_sha = hashlib.sha256(_canonical_json(semantic_payload).encode()).hexdigest()
    matched_constraints = len(set(reference_constraints) & set(compiled_constraints))
    matched_objectives = len(set(reference_objectives) & set(compiled_objectives))
    denominator = len(constraints) + len(objectives)
    reference_denominator = len(reference_constraints) + len(reference_objectives)
    row = {
        "run_id": _relative(trajectory_path.parent),
        "trajectory_path": _relative(trajectory_path),
        "trajectory_sha256": _sha256(trajectory_path),
        "task_id": trajectory.get("task_id"),
        "scenario": task["scenario"],
        "catalog_scenario": task["catalog_scenario"],
        "hard_catalog": task["hard_catalog"],
        "contract_sha256": fingerprint,
        "semantic_contract_sha256": semantic_sha,
        "constraint_count": len(constraints),
        "objective_count": len(objectives),
        "constraint_ids": [item.get("criterion_id") for item in constraints],
        "objective_ids": [item.get("criterion_id") for item in objectives],
        "objective_priorities": priorities,
        "objective_weights": weights,
        "all_objectives_coequal_unweighted": all(value is None for value in (*priorities, *weights)),
        "instruction_matches_trajectory": contract.get("instruction") == trajectory.get("instruction"),
        "instruction_matches_benchmark_reference": contract.get("instruction") == reference["instruction"],
        "schema_concordance": {
            "matched_constraints": matched_constraints,
            "reference_constraints": len(reference_constraints),
            "matched_objectives": matched_objectives,
            "reference_objectives": len(reference_objectives),
            "criterion_precision": matched_constraints + matched_objectives,
            "criterion_precision_denominator": denominator,
            "criterion_precision_value": (
                (matched_constraints + matched_objectives) / denominator
                if denominator else None
            ),
            "criterion_recall": matched_constraints + matched_objectives,
            "criterion_recall_denominator": reference_denominator,
            "criterion_recall_value": (
                (matched_constraints + matched_objectives) / reference_denominator
                if reference_denominator else None
            ),
            "missing_constraints": constraint_missing,
            "missing_objectives": objective_missing,
            "extra_or_contextual": extras,
            "constraint_checks": constraint_checks,
            "objective_checks": objective_checks,
            "all_mapped_operators_values_directions_match": all(
                check["operator_match"] and check["expected_value_match"]
                for check in constraint_checks.values()
            )
            and all(check["direction_match"] for check in objective_checks.values()),
            "scope_note": (
                "Deterministic field/operator/value concordance with preferences.json; "
                "schema extras are reported, not automatically judged incorrect."
            ),
        },
    }
    sources = [
        {"path": _relative(reference["preferences_path"]), "sha256": reference["preferences_sha256"]},
        {"path": _relative(reference["instructions_path"]), "sha256": reference["instructions_sha256"]},
    ]
    return row, sources


def audit_v19_contracts(v19_root: Path, catalog_root: Path) -> dict[str, Any]:
    run_root = v19_root / "runs" if (v19_root / "runs").is_dir() else v19_root
    paths = sorted(
        path
        for path in run_root.rglob("trajectory.json")
        if _load_object(path).get("scaffold") == "browseruse-deliberative"
    )
    if len(paths) != 30:
        raise AnalysisValidationError(f"V19 contract audit expected 30 trajectories, found {len(paths)}")
    rows: list[dict[str, Any]] = []
    reference_sources: dict[str, str] = {}
    canonical_contracts: dict[str, dict[str, Any]] = {}
    for path in paths:
        row, sources = _audit_contract_row(path, catalog_root)
        rows.append(row)
        raw = (( _load_object(path).get("stats") or {}).get("deliberative") or {})[
            "contract_canonical_json"
        ]
        entry = canonical_contracts.setdefault(
            row["contract_sha256"], {"canonical_json": raw, "runs": []}
        )
        entry["runs"].append(row["run_id"])
        for source in sources:
            reference_sources[source["path"]] = source["sha256"]

    grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["task_id"])].append(row)
    stability = {}
    for task_id, members in sorted(grouped.items()):
        exact = sorted({row["contract_sha256"] for row in members})
        semantic = sorted({row["semantic_contract_sha256"] for row in members})
        stability[task_id] = {
            "runs": len(members),
            "unique_exact_canonical_contracts": len(exact),
            "exact_stable": len(exact) == 1,
            "unique_normalized_semantic_contracts": len(semantic),
            "semantic_stable": len(semantic) == 1,
            "contract_sha256s": exact,
            "semantic_contract_sha256s": semantic,
            "constraint_count_distribution": dict(
                sorted(Counter(row["constraint_count"] for row in members).items())
            ),
            "objective_count_distribution": dict(
                sorted(Counter(row["objective_count"] for row in members).items())
            ),
        }

    mattress_rows = [row for row in rows if row["scenario"] == "mattress"]
    contextual_present = [
        row
        for row in mattress_rows
        if {"durability", "support"}.issubset(set(row["constraint_ids"]))
    ]
    contextual_absent = [row for row in mattress_rows if row not in contextual_present]
    negative_case = {
        "name": "mattress_contextual_durability_support_additions",
        "status": "preserved_negative_case",
        "interpretation": (
            "The compiler sometimes promoted persona wording ('durable, supportive') into "
            "boolean constraints. These fields are not in the benchmark preference schema. "
            "They are retained verbatim and reported as schema extras; no contract or run is patched."
        ),
        "present_runs": [
            {
                "run_id": row["run_id"],
                "task_id": row["task_id"],
                "contract_sha256": row["contract_sha256"],
                "constraint_ids": row["constraint_ids"],
            }
            for row in contextual_present
        ],
        "absent_runs": [
            {
                "run_id": row["run_id"],
                "task_id": row["task_id"],
                "contract_sha256": row["contract_sha256"],
                "constraint_ids": row["constraint_ids"],
            }
            for row in contextual_absent
        ],
    }
    return {
        "schema": "agentarena.v19-contract-audit.v1",
        "method": {
            "model_as_judge": False,
            "exact_contract_identity": "length-prefixed SHA-256 used by the production TaskContract",
            "semantic_stability": (
                "Deterministic alias-normalized criterion IDs, operators, expected values, "
                "objective directions/priorities/weights, and search mode; prose excluded."
            ),
            "benchmark_concordance": (
                "preferences.json graded constraints/objectives plus instructions.json exact text"
            ),
        },
        "audited_contract_runs": len(rows),
        "unique_canonical_contract_count": len(canonical_contracts),
        "all_objectives_coequal_unweighted": all(
            row["all_objectives_coequal_unweighted"] for row in rows
        ),
        "criterion_count_distribution": dict(
            sorted(Counter(row["constraint_count"] + row["objective_count"] for row in rows).items())
        ),
        "priority_patterns": dict(
            sorted(Counter(str(row["objective_priorities"]) for row in rows).items())
        ),
        "weight_patterns": dict(
            sorted(Counter(str(row["objective_weights"]) for row in rows).items())
        ),
        "repeated_task_stability": stability,
        "contextual_addition_negative_case": negative_case,
        "unique_canonical_contracts": {
            key: value for key, value in sorted(canonical_contracts.items())
        },
        "runs": sorted(rows, key=lambda row: row["run_id"]),
        "reference_sources": [
            {"path": path, "sha256": digest}
            for path, digest in sorted(reference_sources.items())
        ],
    }


SIGNAL_BOOLEAN_FIELDS = (
    "product_api_attempt",
    "same_origin_fetch",
    "bulk_detail_fetch",
    "todo_or_plan_file",
    "evidence_file",
    "exhaustive_claim",
    "lexicographic_claim",
    "checkout_recheck_claim",
    "explicit_comparison_claim",
    "action_error_signal",
    "retrieval_failure_signal",
    "unknown_or_unresolved_signal",
    "repeated_action_loop",
)


def _mean(values: Iterable[float]) -> float | None:
    materialized = list(values)
    return round(statistics.mean(materialized), 6) if materialized else None


def _aggregate(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    included = [row for row in records if row["analysis_included"]]
    transacted = [row for row in included if row["chosen"] is not None]
    gate_known = [row for row in included if row["G"] is not None]
    opt_known = [row for row in included if row["O"] is not None]
    return {
        "inventory_runs": len(records),
        "infra_excluded": len(records) - len(included),
        "measured_runs": len(included),
        "transactions": len(transacted),
        "behavioral_no_orders": sum(row["chosen"] is None for row in included),
        "mean_preservation_strict": _mean(float(row["preservation_strict"]) for row in included),
        "strict_success_rate": _mean(float(row["strict_binary"]) for row in included),
        "literal_hero_rate": _mean(float(row["literal_hero"]) for row in included),
        "gate_known_runs": len(gate_known),
        "gate_failure_runs": sum(row["G"] == 0 for row in gate_known),
        "optimality_known_runs": len(opt_known),
        "optimality_shortfall_runs": sum(float(row["O"]) < 1.0 - 1e-9 for row in opt_known),
        "mean_O_when_known": _mean(float(row["O"]) for row in opt_known),
        "signal_run_counts": {
            field: sum(bool(row["signals"][field]) for row in included)
            for field in SIGNAL_BOOLEAN_FIELDS
        },
        "basket_run_counts": {
            "constraint_violation": sum(row["basket"]["hard_constraint_violation"] for row in included),
            "extra_line_items": sum(row["basket"]["extra_line_items"] > 0 for row in included),
            "add_on": sum(row["basket"]["add_on_count"] > 0 for row in included),
            "service_fee": sum(row["basket"]["service_fee"] > 0 for row in included),
        },
        "step_semantics": dict(
            sorted(Counter(row["step_accounting"]["semantics"] for row in records).items())
        ),
    }


def _group_aggregate(
    records: Sequence[Mapping[str, Any]], keys: Sequence[str]
) -> dict[str, Any]:
    groups: defaultdict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for row in records:
        groups[tuple(row.get(key) for key in keys)].append(row)
    return {
        "/".join(str(value) for value in group): _aggregate(members)
        for group, members in sorted(groups.items(), key=lambda item: tuple(str(v) for v in item[0]))
    }


def _source_snapshot_sha(records: Sequence[Mapping[str, Any]], contract_audit: Mapping[str, Any]) -> str:
    sources: dict[str, str] = {}
    for row in records:
        provenance = row["provenance"]
        for prefix in ("summary", "trajectory", "catalog_pool"):
            sources[provenance[f"{prefix}_path"]] = provenance[f"{prefix}_sha256"]
    for source in contract_audit["reference_sources"]:
        sources[source["path"]] = source["sha256"]
    payload = [{"path": path, "sha256": digest} for path, digest in sorted(sources.items())]
    return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()


def _csv_text(records: Sequence[Mapping[str, Any]]) -> str:
    fields = [
        "cohort", "source_campaign", "run_id", "pair_id", "arm", "model", "scaffold",
        "task_id", "scenario", "catalog_scenario", "variant", "relative_level", "condition",
        "outcome", "chosen", "literal_hero_asin", "literal_hero", "selected_role",
        "preservation_strict", "strict_binary", "G", "O", "decomposition_source",
        "analysis_included", "infra_class", "infra_code", "step_semantics",
        "summary_num_steps", "decision_steps", "tool_actions", "legacy_flattened_actions",
        "seconds", "candidate_id_count", "candidate_ids", "pdp_candidate_count",
        "visited_pdp_candidate_count", "search_page_count", "max_search_page_seen",
        *SIGNAL_BOOLEAN_FIELDS,
        "line_item_count", "total_quantity", "extra_line_items", "add_on_count", "service_fee",
        "hard_constraint_violation", "all_in_total", "summary_path", "summary_sha256",
        "trajectory_path", "trajectory_sha256", "catalog_pool_path", "catalog_pool_sha256",
    ]
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in sorted(records, key=lambda item: item["run_id"]):
        signals = row["signals"]
        basket = row["basket"]
        steps = row["step_accounting"]
        provenance = row["provenance"]
        flat = {
            key: row.get(key)
            for key in (
                "cohort", "source_campaign", "run_id", "pair_id", "arm", "model", "scaffold",
                "task_id", "scenario", "catalog_scenario", "variant", "relative_level", "condition",
                "outcome", "chosen", "literal_hero_asin", "literal_hero", "selected_role",
                "preservation_strict", "strict_binary", "G", "O", "decomposition_source",
                "analysis_included", "seconds",
            )
        }
        flat.update(
            {
                "infra_class": row["infra"].get("class"),
                "infra_code": row["infra"].get("code"),
                "step_semantics": steps["semantics"],
                "summary_num_steps": steps["summary_num_steps"],
                "decision_steps": steps["decision_steps"],
                "tool_actions": steps["tool_actions"],
                "legacy_flattened_actions": steps["legacy_flattened_actions"],
                "candidate_id_count": signals["candidate_id_count"],
                "candidate_ids": "|".join(signals["candidate_ids"]),
                "pdp_candidate_count": signals["pdp_candidate_count"],
                "visited_pdp_candidate_count": signals["visited_pdp_candidate_count"],
                "search_page_count": signals["search_page_count"],
                "max_search_page_seen": signals["max_search_page_seen"],
                **{field: signals[field] for field in SIGNAL_BOOLEAN_FIELDS},
                **{key: basket[key] for key in (
                    "line_item_count", "total_quantity", "extra_line_items", "add_on_count",
                    "service_fee", "hard_constraint_violation", "all_in_total",
                )},
                **provenance,
            }
        )
        writer.writerow(flat)
    return buffer.getvalue()


def build_analysis(
    overhaul_root: Path = DEFAULT_OVERHAUL,
    v19_root: Path = DEFAULT_V19,
    hard_root: Path = DEFAULT_HARD,
    catalog_root: Path = DEFAULT_CATALOGS,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
    catalogs = _catalog_cache(catalog_root)
    overhaul, overhaul_inventory = scan_overhaul(overhaul_root, catalogs)
    v19, v19_inventory = scan_v19(v19_root, catalogs)
    hard, hard_inventory = scan_canonical_hard(hard_root, catalogs)
    contract_audit = audit_v19_contracts(v19_root, catalog_root)
    records = sorted((*overhaul, *v19, *hard), key=lambda row: row["run_id"])
    snapshot_sha = _source_snapshot_sha(records, contract_audit)
    runs_document = {
        "schema": "agentarena.mixed-method-runs.v1",
        "source_snapshot_sha256": snapshot_sha,
        "run_count": len(records),
        "runs": records,
    }
    summary = {
        "schema": "agentarena.mixed-method-summary.v1",
        "source_snapshot_sha256": snapshot_sha,
        "method": {
            "model_as_judge": False,
            "overhaul_primary_scope": ["L1/mixed", "L2/graded", "L3/graded3", "L4/graded4"],
            "l0_policy": (
                "L0/thresholded is matrix-audited but excluded from the non-absolute mode cohort: "
                "O=1 by definition and strict_binary=1 is not literal-hero identity."
            ),
            "literal_hero": "1 iff effective chosen ASIN equals the unique pool decoy_kind=hero ASIN",
            "score_policy": (
                "Stored preservation_strict/strict_binary required for purchases; behavioral no-orders "
                "are zero; canonical infra exclusions and the exact prior user-directed Qwen zero are reused."
            ),
            "G_O_policy": (
                "Standard-catalog purchases are recomputed from criteria and checked against stored P*. "
                "Hard positive P* gives G=1,O=P* algebraically; unidentified zero factors/no-orders stay null."
            ),
            "step_policy": (
                "Historical summaries without separate counters are labeled flattened atomic tool actions; "
                "current trajectories retain decision_steps and tool_actions separately."
            ),
            "signal_policy": (
                "Deterministic conservative regex/action proxies; evaluator basket/violations are exact. "
                "Proxy presence is not a thematic interpretation or causal claim."
            ),
        },
        "inventory": {
            "overhaul": overhaul_inventory,
            "v19": v19_inventory,
            "canonical_hard": hard_inventory,
            "exported_run_rows": len(records),
        },
        "overall": _aggregate(records),
        "by_cohort": _group_aggregate(records, ("cohort",)),
        "overhaul_by_condition": _group_aggregate(overhaul, ("condition",)),
        "overhaul_by_level_condition": _group_aggregate(
            overhaul, ("relative_level", "condition")
        ),
        "v19_by_cohort_arm": _group_aggregate(v19, ("cohort", "arm")),
        "canonical_hard_by_scenario": _group_aggregate(hard, ("scenario",)),
        "contract_audit": {
            "audited_contract_runs": contract_audit["audited_contract_runs"],
            "unique_canonical_contract_count": contract_audit["unique_canonical_contract_count"],
            "all_objectives_coequal_unweighted": contract_audit[
                "all_objectives_coequal_unweighted"
            ],
            "negative_case": contract_audit["contextual_addition_negative_case"]["name"],
        },
    }
    return runs_document, summary, contract_audit, _csv_text(records)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content)
    temporary.replace(path)


def write_analysis(
    output: Path,
    runs_document: Mapping[str, Any],
    summary: Mapping[str, Any],
    contract_audit: Mapping[str, Any],
    csv_text: str,
) -> dict[str, Any]:
    artifacts = {
        "runs.json": _canonical_json(runs_document, pretty=True),
        "runs.csv": csv_text,
        "summary.json": _canonical_json(summary, pretty=True),
        "v19_contract_audit.json": _canonical_json(contract_audit, pretty=True),
    }
    hashes: dict[str, str] = {}
    for name, content in artifacts.items():
        path = output / name
        _atomic_write(path, content)
        digest = _sha256(path)
        hashes[name] = digest
        _atomic_write(output / f"{name}.sha256", f"{digest}  {name}\n")
    manifest = {
        "schema": "agentarena.mixed-method-artifact-manifest.v1",
        "source_snapshot_sha256": summary["source_snapshot_sha256"],
        "artifacts": [
            {"path": name, "sha256": digest} for name, digest in sorted(hashes.items())
        ],
    }
    _atomic_write(output / "manifest.json", _canonical_json(manifest, pretty=True))
    manifest_digest = _sha256(output / "manifest.json")
    _atomic_write(output / "manifest.json.sha256", f"{manifest_digest}  manifest.json\n")
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--overhaul-root", type=Path, default=DEFAULT_OVERHAUL)
    parser.add_argument("--v19-root", type=Path, default=DEFAULT_V19)
    parser.add_argument("--hard-root", type=Path, default=DEFAULT_HARD)
    parser.add_argument("--catalog-root", type=Path, default=DEFAULT_CATALOGS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        products = build_analysis(
            args.overhaul_root.resolve(),
            args.v19_root.resolve(),
            args.hard_root.resolve(),
            args.catalog_root.resolve(),
        )
        manifest = write_analysis(args.output.resolve(), *products)
    except AnalysisValidationError as exc:
        print(f"analysis refused source snapshot: {exc}", file=sys.stderr)
        return 2
    summary = products[1]
    inventory = summary["inventory"]
    print(args.output.resolve())
    print(
        "overhaul non-absolute measured "
        f"{inventory['overhaul']['non_absolute_measured_cells']}/"
        f"{inventory['overhaul']['non_absolute_inventory_cells']}; "
        f"V19={inventory['v19']['inventory_cells']}; "
        f"canonical hard={inventory['canonical_hard']['inventory_cells']}"
    )
    print(f"source snapshot sha256={manifest['source_snapshot_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
