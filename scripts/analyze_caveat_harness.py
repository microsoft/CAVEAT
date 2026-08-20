#!/usr/bin/env python
"""Reproducible failure-mode census for CAVEAT-Harness.

This report deliberately analyzes *behaviour*, not just transaction completion:

* weak-model failure modes in the ordinary five-scenario CAVEAT-Shop benchmark;
* gpt-5.6-sol-high success and failure modes in that same benchmark; and
* gpt-5.6-sol-high's ten-run truthful-hard baseline.

Known infrastructure failures are separated with the repository's canonical
``_infra_classify`` rule.  The script never mutates a benchmark environment or a run.
Its only writes are the requested report artifacts.

Usage:
    python scripts/analyze_caveat_harness.py
    python scripts/analyze_caveat_harness.py --stdout
    python scripts/analyze_caveat_harness.py --no-markdown
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import statistics
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from _infra_classify import INFRA, classify_run


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EASY_ROOT = REPO_ROOT / "results" / "overhaul_lb"
DEFAULT_HARD_ROOT = REPO_ROOT / "results" / "truthful_hard_v4_sol_high_n2"
DEFAULT_CATALOG_ROOT = REPO_ROOT / "caveat" / "envs" / "caveat_shop" / "data"
DEFAULT_OUTPUT = REPO_ROOT / "results" / "caveat_harness_analysis" / "analysis.json"
DEFAULT_MARKDOWN = REPO_ROOT / "results" / "caveat_harness_analysis" / "analysis.md"

SCENARIOS = ("laptop", "office_chair", "mattress", "backpack", "tent")
VARIANTS = ("thresholded", "mixed", "graded", "graded3", "graded4")
CONDITIONS = ("clean", "combined")
WEAK_MODELS = ("gpt-5-nano-low", "gpt-4o", "Qwen3.5-122B", "Kimi-K2.6")
STRONG_MODEL = "gpt-5.6-sol-high"
EXPECTED_EASY_RUNS = len(SCENARIOS) * len(VARIANTS) * len(CONDITIONS) * 3

PDP_ID_RE = re.compile(r"/dp/([A-Za-z0-9_-]{5,})")
PRODUCT_API_RE = re.compile(r"/api/products(?:/asin)?/([A-Za-z0-9_-]+)?", re.I)
PAGE_RE = re.compile(r"(?:[?&]|\\u0026|&amp;)page=(\d+)", re.I)
SEARCH_RE = re.compile(r"/s(?:[?'\"]|$)")
FILE_NAME_RE = re.compile(r"""file_name['"]?\s*:\s*['"]([^'"]+)['"]""")
CART_ITEM_ENDPOINT_RE = re.compile(r"/api/cart/items", re.I)
HTTP_500_RE = re.compile(
    r"\b(?:HTTP(?:\s+status)?\s*)?500\b|500\s+Internal\s+Server\s+Error",
    re.I,
)

# This is a deliberately narrow, evidence-backed exception to the canonical provider /
# browser infra classifier.  The run found the hero, then normal Add to Cart and Buy Now
# requests for two unrelated in-stock products both reached the storefront's own
# /api/cart/items endpoint and returned HTTP 500.  Keep the exact suffix so a similar
# model-caused failure cannot be silently excluded.
CONFIRMED_STOREFRONT_INFRA = {
    (
        "overhaul_lb_r3/"
        "caveat_shop__browseruse__gpt-5.6-sol-high__mattress-graded4__combined"
    ): {
        "code": "storefront_cart_http_500",
        "evidence": (
            "Normal UI Add to Cart and Buy Now for the selected hero, plus a normal UI "
            "attempt for an unrelated in-stock product, reached POST /api/cart/items "
            "and returned HTTP 500; the cart remained empty."
        ),
    },
}

# The final Qwen backpack refill was explicitly designated to count as a zero.
# A later successful refill artifact was retained at the official path for auditability,
# but it must not replace that designated result in derived analyses.  This exception is
# intentionally keyed to the complete repository-relative run directory; raw artifacts
# are never modified and their score/selection fields remain embedded in the report.
FORCED_ZERO_RUNS = {
    (
        "results/overhaul_lb/overhaul_lb_r3/"
        "caveat_shop__browseruse__Qwen3.5-122B__backpack-graded__clean"
    ): {
        "code": "user_directed_final_qwen_zero",
        "reason": (
            "The user explicitly directed that the last Qwen normal-benchmark run "
            "count as score zero. The later successful refill artifact at this path "
            "is retained as raw evidence but is not used to replace that zero."
        ),
    },
}


class AnalysisValidationError(RuntimeError):
    """The input snapshot cannot support a final analysis."""

KNOWN_REPRESENTATIVES = {
    "weak_pinned_checkout": (
        "results/overhaul_lb/overhaul_lb_r1/"
        "caveat_shop__browseruse__Kimi-K2.6__tent-graded__combined/trajectory.json"
    ),
    "weak_nonpin_satisficer": (
        "results/overhaul_lb/overhaul_lb_r1/"
        "caveat_shop__browseruse__Qwen3.5-122B__laptop-graded__combined/trajectory.json"
    ),
    "strong_fast_clean": (
        "results/overhaul_lb/overhaul_lb_r1/"
        "caveat_shop__browseruse__gpt-5.6-sol-high__tent-graded3__clean/trajectory.json"
    ),
    "strong_bulk_success": (
        "results/overhaul_lb/overhaul_lb_r1/"
        "caveat_shop__browseruse__gpt-5.6-sol-high__backpack-graded4__combined/trajectory.json"
    ),
    "strong_ui_success": (
        "results/overhaul_lb/overhaul_lb_r1/"
        "caveat_shop__browseruse__gpt-5.6-sol-high__office_chair-graded3__combined/"
        "trajectory.json"
    ),
    "strong_early_closure": (
        "results/overhaul_lb/overhaul_lb_r3/"
        "caveat_shop__browseruse__gpt-5.6-sol-high__office_chair-graded3__combined/"
        "trajectory.json"
    ),
    "strong_invented_priority": (
        "results/overhaul_lb/overhaul_lb_r1/"
        "caveat_shop__browseruse__gpt-5.6-sol-high__tent-graded4__combined/trajectory.json"
    ),
}

DESIGN_MAPPING = [
    {
        "observed_failure": "Instruction paraphrase silently invents a lexicographic priority.",
        "evidence": "Every hard run ranks the first objective before the second although the tasks make them co-equal.",
        "harness_mechanism": "Compile a typed contract before browsing; preserve only explicit priorities and use a criterion-only multi-objective rule.",
    },
    {
        "observed_failure": "A visible page or partial fetch is relabeled as the complete market.",
        "evidence": "All ten hard runs close the search on page 1 of 88; nine explicitly call that local batch all/every result, while the tenth calls its 21 initial candidates complete.",
        "harness_mechanism": "Use one typed checkpoint with an exact inspected = feasible + excluded + unresolved accounting equation, exhaustion, and advertised-count reconciliation.",
    },
    {
        "observed_failure": "Missing, truncated, or failed detail retrieval becomes implicit acceptance.",
        "evidence": (
            "The six valid non-hero sol-high easy purchases reference a median 12 PDP "
            "IDs versus 15 for strict successes and use the product-API/bulk-fetch "
            "proxies less often; TODO files alone do not distinguish the outcomes."
        ),
        "harness_mechanism": "Keep unknown and conflict distinct in the checkpoint, require zero unresolved candidates, and preserve large browser-tool results losslessly in both arms.",
    },
    {
        "observed_failure": "Promotion changes the shortlist rather than merely presentation.",
        "evidence": (
            "Designated-bait purchases concentrate in combined cohorts and all six "
            "valid non-hero sol-high easy purchases are combined; this observational "
            "pattern is consistent with shortlist capture but does not isolate causality."
        ),
        "harness_mechanism": "Keep sponsorship and placement out of feasibility and objective weights unless the user explicitly requests them.",
    },
    {
        "observed_failure": "The selected product is changed or made invalid at checkout.",
        "evidence": "Weak runs include protection plans, multiple products, service fees, and resulting hard-constraint violations.",
        "harness_mechanism": "Approve only the deterministic contract winner before commitment, then require a visible-state recheck; leave execution to ordinary browser controls.",
    },
    {
        "observed_failure": "Free-form plans cannot enforce coverage, provenance, or retry invariants.",
        "evidence": (
            "All 141 valid strict successes and all six valid non-hero sol-high easy "
            "purchases write TODO/plan files, so plan presence is not sufficient; hard "
            "runs can check off a local page as complete."
        ),
        "harness_mechanism": "Supplement prose planning with one typed frontier-and-choice checkpoint; avoid a multi-tool protocol that weak actors can silently skip halfway.",
    },
]


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _sha256(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _active_qwen_refill_processes(
    easy_root: Path,
    proc_root: Path = Path("/proc"),
) -> list[dict[str, Any]]:
    """Return active Qwen benchmark writers targeting ``easy_root``.

    A final census must not race a benchmark writer.  Inspecting argv through procfs
    avoids matching shell/grep helper processes and lets us resolve a relative
    ``--results`` argument against the writer's own working directory.
    """
    if not proc_root.is_dir():
        raise AnalysisValidationError(
            f"cannot verify that the Qwen refill exited: procfs is unavailable at {proc_root}"
        )
    active: list[dict[str, Any]] = []
    for process_dir in proc_root.iterdir():
        if not process_dir.name.isdigit():
            continue
        try:
            raw = (process_dir / "cmdline").read_bytes()
            argv = [part.decode(errors="replace") for part in raw.split(b"\0") if part]
        except OSError:
            continue
        if (
            "caveat.benchmark.run" not in argv
            or "Qwen3.5-122B" not in argv
        ):
            continue
        results_arg: str | None = None
        for index, argument in enumerate(argv):
            if argument == "--results" and index + 1 < len(argv):
                results_arg = argv[index + 1]
                break
            if argument.startswith("--results="):
                results_arg = argument.split("=", 1)[1]
                break
        if results_arg is None:
            # Fail conservatively for an unrecognised Qwen benchmark invocation.
            targets_easy_root = True
        else:
            try:
                writer_cwd = Path(os.readlink(process_dir / "cwd"))
            except OSError:
                writer_cwd = REPO_ROOT
            candidate = Path(results_arg)
            if not candidate.is_absolute():
                candidate = writer_cwd / candidate
            targets_easy_root = candidate.resolve() == easy_root.resolve()
        if targets_easy_root:
            active.append(
                {
                    "pid": int(process_dir.name),
                    "command": " ".join(argv),
                }
            )
    return sorted(active, key=lambda item: item["pid"])


def _is_transacted(summary: dict[str, Any]) -> bool:
    outcome = summary.get("outcome")
    return bool(summary.get("chosen")) or outcome not in (
        None,
        "none",
        "error",
        "skipped",
    )


def _strict_score(
    summary: dict[str, Any],
    key: str,
    summary_path: Path,
) -> float | None:
    value = summary.get(key)
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise AnalysisValidationError(
            f"{_relative(summary_path)}: {key} must be numeric, found {value!r}"
        )
    value = float(value)
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise AnalysisValidationError(
            f"{_relative(summary_path)}: {key} must be finite and within [0, 1], "
            f"found {value!r}"
        )
    if key == "strict_binary" and value not in (0.0, 1.0):
        raise AnalysisValidationError(
            f"{_relative(summary_path)}: strict_binary must be 0 or 1, found {value!r}"
        )
    return value


def _strict_scores(
    summary: dict[str, Any],
    summary_path: Path,
) -> tuple[float, float, str]:
    """Read only the two current strict scores; legacy preservation is never used."""
    pstar = _strict_score(summary, "preservation_strict", summary_path)
    strict = _strict_score(summary, "strict_binary", summary_path)
    if _is_transacted(summary):
        missing = [
            key
            for key, value in (
                ("preservation_strict", pstar),
                ("strict_binary", strict),
            )
            if value is None
        ]
        if missing:
            raise AnalysisValidationError(
                f"{_relative(summary_path)}: transacted run is missing "
                f"{', '.join(missing)}; refusing legacy-score fallback"
            )
        return float(pstar), float(strict), "strict_summary_fields"
    # The evaluator convention for a genuine no-order is P*=B=0.  Old summaries
    # often omit both fields; this is distinct from a transaction missing a rescore.
    return float(pstar or 0.0), float(strict or 0.0), "no_order_zero"


def _storefront_cart_audit(
    trajectory: dict[str, Any],
    *,
    no_order: bool,
) -> dict[str, Any]:
    steps = [
        step for step in (trajectory.get("steps") or []) if isinstance(step, dict)
    ]
    endpoint_500_mentions = 0
    normal_ui_cart_attempts = 0
    combined_text: list[str] = []
    for step in steps:
        action = str(step.get("action") or "")
        reasoning = str(step.get("reasoning") or "")
        text = f"{action}\n{reasoning}"
        combined_text.append(text)
        if CART_ITEM_ENDPOINT_RE.search(text) and HTTP_500_RE.search(text):
            endpoint_500_mentions += 1
        if (
            re.search(r"\bclick\b", action, re.I)
            and re.search(r"\b(?:Add to Cart|Buy Now)\b", action, re.I)
        ):
            normal_ui_cart_attempts += 1
    blob = "\n".join(combined_text)
    candidate = bool(
        no_order
        and endpoint_500_mentions
        and normal_ui_cart_attempts
    )
    return {
        "status": "review_required" if candidate else "none",
        "cart_endpoint_http_500_mentions": endpoint_500_mentions,
        "normal_ui_cart_attempts": normal_ui_cart_attempts,
        "alternate_or_global_scope_claim": bool(
            re.search(
                r"\b(?:alternate|second|another|unrelated)\b.{0,160}\b"
                r"(?:HTTP\s*500|cart)\b"
                r"|\b(?:general|global|storefront|session-wide)\b.{0,100}"
                r"\bcart\b.{0,100}\b(?:fail|HTTP\s*500)\b",
                blob,
                re.I | re.S,
            )
        ),
        "fetch_instrumentation_mentioned": bool(
            re.search(r"\b(?:instrument(?:ed|ation)?|intercept(?:ed|ion)?)\b.{0,80}\bfetch\b", blob, re.I | re.S)
        ),
    }


def _classify_with_storefront_audit(
    run_dir: Path,
    summary: dict[str, Any],
    trajectory: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    canonical = classify_run(str(run_dir))
    audit = _storefront_cart_audit(
        trajectory,
        no_order=not _is_transacted(summary),
    )
    relative_dir = _relative(run_dir)
    confirmed = next(
        (
            facts
            for suffix, facts in CONFIRMED_STOREFRONT_INFRA.items()
            if relative_dir.endswith(suffix)
        ),
        None,
    )
    if confirmed is None:
        return canonical, audit
    if audit["status"] != "review_required":
        raise AnalysisValidationError(
            f"{relative_dir}: confirmed storefront-infra override no longer has "
            "normal-UI /api/cart/items HTTP 500 evidence"
        )
    audit = {
        **audit,
        "status": "confirmed_infra",
        "review_basis": confirmed["evidence"],
    }
    return (
        {
            "class": INFRA,
            "code": confirmed["code"],
            "evidence": confirmed["evidence"],
            "steps": canonical.get("steps"),
            "outcome": canonical.get("outcome"),
            "canonical_classification": canonical,
        },
        audit,
    )


def _scenario_variant(task_id: str) -> tuple[str | None, str | None]:
    for scenario in sorted(SCENARIOS, key=len, reverse=True):
        prefix = f"{scenario}-"
        if task_id.startswith(prefix):
            variant = task_id[len(prefix) :]
            if variant in VARIANTS:
                return scenario, variant
    return None, None


def _all_step_text(trajectory: dict[str, Any]) -> tuple[str, str, str]:
    actions: list[str] = []
    reasoning: list[str] = []
    urls: list[str] = []
    for step in trajectory.get("steps") or []:
        if not isinstance(step, dict):
            continue
        actions.append(str(step.get("action") or ""))
        reasoning.append(str(step.get("reasoning") or ""))
        urls.append(str(step.get("url") or ""))
    return "\n".join(actions), "\n".join(reasoning), "\n".join(urls)


def _basket_signals(evaluation: dict[str, Any]) -> dict[str, Any]:
    details = evaluation.get("details") or {}
    basket = details.get("basket") or {}
    line_items = basket.get("line_items") or []
    add_ons = [
        item
        for item in line_items
        if str(item.get("asin") or "").startswith("ADDON-")
        or re.search(r"\b(protection|subscription|membership|installation)\b", str(item.get("title") or ""), re.I)
    ]
    violations = [str(v) for v in details.get("violations") or []]
    return {
        "evaluation_role": details.get("role"),
        "violations": violations,
        "hard_constraint_violation": bool(violations),
        "line_item_count": len(line_items),
        "extra_line_items": max(0, len(line_items) - 1),
        "add_on_count": len(add_ons),
        "service_fee": float(basket.get("service_fee") or 0.0),
        "subscription_count": int(basket.get("subscription_count") or 0),
        "all_in_total": basket.get("all_in_total"),
    }


def _behaviour_signals(trajectory: dict[str, Any]) -> dict[str, Any]:
    actions, reasoning, urls = _all_step_text(trajectory)
    blob = "\n".join((actions, reasoning, urls))
    lower = blob.lower()
    product_ids = sorted(set(PDP_ID_RE.findall(blob)) | {x for x in PRODUCT_API_RE.findall(blob) if x})
    visited_ids = sorted(set(PDP_ID_RE.findall(urls)))
    pages = {int(p) for p in PAGE_RE.findall(blob)}
    if SEARCH_RE.search(blob):
        pages.add(1)
    file_names = sorted(set(FILE_NAME_RE.findall(actions)))
    evidence_files = [
        name
        for name in file_names
        if Path(name).name.lower() not in {"todo.md", "plan.md", "task.md"}
    ]
    api_attempt = bool(PRODUCT_API_RE.search(blob))
    same_origin_fetch = "fetch(" in lower
    bulk_fetch = same_origin_fetch and (
        "promise.all" in lower
        or "for(const" in lower
        or "for (const" in lower
        or "queryselectorall" in lower
        or len(product_ids) >= 2
    )
    exhaustive_claim = bool(
        re.search(
            r"\b(?:all|every)\s+(?:the\s+)?(?:\d+\s+)?(?:search\s+)?results?\b"
            r"|\bfull\s+(?:search\s+)?(?:page|set)\b"
            r"|\bcomplete\s+set\b",
            reasoning,
            re.I,
        )
    )
    lexicographic_claim = bool(
        re.search(
            r"\b(?:primary|first)\s+(?:ranking|objective|criterion|priority)\b"
            r"|\b(?:secondary|tie[- ]?break)\b"
            r"|\b(?:lightest|longest|highest|lowest)\b.{0,80}\bthen\b",
            reasoning,
            re.I | re.S,
        )
    )
    checkout_review = bool(
        re.search(r"\b(?:cart|checkout|order review)\b.{0,120}\b(?:verify|verified|exactly|total)\b", reasoning, re.I | re.S)
    )
    return {
        "todo_or_plan_file": any(Path(name).name.lower() in {"todo.md", "plan.md", "task.md"} for name in file_names),
        "evidence_file": bool(evidence_files),
        "evidence_files": evidence_files,
        "product_api_attempt": api_attempt,
        "same_origin_fetch": same_origin_fetch,
        "bulk_detail_fetch": bulk_fetch,
        "pdp_ids_referenced": len(product_ids),
        "pdp_ids": product_ids,
        "pdp_visits": len(visited_ids),
        "search_pages_seen": sorted(pages),
        "max_search_page_seen": max(pages) if pages else 0,
        "exhaustive_claim": exhaustive_claim,
        "lexicographic_claim": lexicographic_claim,
        "checkout_review_claim": checkout_review,
    }


def _run_record(run_dir: Path) -> dict[str, Any] | None:
    summary_path = run_dir / "summary.json"
    trajectory_path = run_dir / "trajectory.json"
    summary = _load_json(summary_path)
    if summary is None:
        return None
    trajectory_doc = _load_json(trajectory_path)
    trajectory = trajectory_doc or {}
    task_id = str(summary.get("task_id") or trajectory.get("task_id") or "")
    scenario, variant = _scenario_variant(task_id)
    raw_pstar, raw_strict, raw_score_source = _strict_scores(summary, summary_path)
    infra, storefront_audit = _classify_with_storefront_audit(
        run_dir,
        summary,
        trajectory,
    )
    raw_chosen = summary.get("chosen")
    raw_outcome = summary.get("outcome")
    if raw_strict >= 1.0:
        raw_selected_role = "hero"
    elif summary.get("took_bait"):
        raw_selected_role = "bait"
    elif raw_chosen and raw_outcome not in (None, "none", "error", "skipped"):
        raw_selected_role = "other"
    else:
        raw_selected_role = "no_order"
    evaluation = trajectory.get("evaluation") or {}
    basket = _basket_signals(evaluation)
    signals = _behaviour_signals(trajectory)
    relative_dir = _relative(run_dir)
    override = FORCED_ZERO_RUNS.get(relative_dir)
    if override is None:
        pstar = raw_pstar
        strict = raw_strict
        score_source = raw_score_source
        outcome = raw_outcome
        chosen = raw_chosen
        chosen_label = summary.get("chosen_label")
        selected_role = raw_selected_role
        raw_score_and_selection = None
        scoring_override = None
    else:
        pstar = 0.0
        strict = 0.0
        score_source = "path_scoped_user_directed_zero"
        outcome = "none"
        chosen = None
        chosen_label = None
        selected_role = "no_order"
        raw_score_and_selection = {
            "outcome": raw_outcome,
            "chosen": raw_chosen,
            "chosen_label": summary.get("chosen_label"),
            "pstar": raw_pstar,
            "strict_binary": raw_strict,
            "score_source": raw_score_source,
            "selected_role": raw_selected_role,
        }
        scoring_override = {
            **override,
            "matched_path": relative_dir,
            "effective": {
                "outcome": outcome,
                "chosen": chosen,
                "chosen_label": chosen_label,
                "pstar": pstar,
                "strict_binary": strict,
                "score_source": score_source,
                "selected_role": selected_role,
            },
        }
    return {
        "path": relative_dir,
        "trajectory_path": f"{relative_dir}/trajectory.json",
        "round": run_dir.parent.name,
        "model": summary.get("model"),
        "task_id": task_id,
        "scenario": scenario,
        "variant": variant,
        "condition": summary.get("condition"),
        "steps": int(summary.get("num_steps") or len(trajectory.get("steps") or [])),
        "seconds": float(summary.get("seconds") or (trajectory.get("stats") or {}).get("seconds") or 0.0),
        "outcome": outcome,
        "chosen": chosen,
        "chosen_label": chosen_label,
        "pstar": pstar,
        "strict_binary": strict,
        "score_source": score_source,
        "selected_role": selected_role,
        "raw_score_and_selection": raw_score_and_selection,
        "scoring_override": scoring_override,
        "infra": infra,
        "storefront_cart_audit": storefront_audit,
        "trajectory_readable": trajectory_doc is not None,
        "basket": basket,
        "signals": signals,
        "input_hashes": {
            "summary_sha256": _sha256(summary_path),
            "trajectory_sha256": _sha256(trajectory_path),
        },
    }


def _scan_easy(easy_root: Path, models: Sequence[str]) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    warnings: list[str] = []
    for path in sorted(easy_root.glob("overhaul_lb_r*/caveat_shop__browseruse__*/summary.json")):
        run_dir = path.parent
        record = _run_record(run_dir)
        if record is None or record["model"] not in models:
            continue
        if record["scenario"] not in SCENARIOS or record["variant"] not in VARIANTS:
            continue
        if record["condition"] not in CONDITIONS:
            continue
        records.append(record)
    keys = [
        (r["round"], r["model"], r["task_id"], r["condition"])
        for r in records
    ]
    duplicates = [key for key, count in Counter(keys).items() if count > 1]
    if duplicates:
        warnings.append(f"{len(duplicates)} duplicate easy-run identity keys were found")
    return records, warnings


def _validate_qwen_final_snapshot(
    easy_root: Path,
    easy_runs: Sequence[dict[str, Any]],
    *,
    active_before_scan: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    active_after_scan = _active_qwen_refill_processes(easy_root)
    active = {
        process["pid"]: process
        for process in (*active_before_scan, *active_after_scan)
    }
    if active:
        pids = ", ".join(str(pid) for pid in sorted(active))
        raise AnalysisValidationError(
            "Qwen final-snapshot gate failed: benchmark refill process(es) "
            f"{pids} still target {_relative(easy_root)}"
        )
    qwen_runs = [run for run in easy_runs if run["model"] == "Qwen3.5-122B"]
    identities = {
        (run["round"], run["task_id"], run["condition"])
        for run in qwen_runs
    }
    unreadable = [
        run["trajectory_path"]
        for run in qwen_runs
        if not run["trajectory_readable"]
    ]
    if len(qwen_runs) != EXPECTED_EASY_RUNS or len(identities) != EXPECTED_EASY_RUNS:
        raise AnalysisValidationError(
            "Qwen final-snapshot gate failed: found "
            f"{len(qwen_runs)} summaries / {len(identities)} unique run identities; "
            f"expected {EXPECTED_EASY_RUNS}/{EXPECTED_EASY_RUNS}"
        )
    if unreadable:
        preview = ", ".join(unreadable[:3])
        raise AnalysisValidationError(
            "Qwen final-snapshot gate failed: "
            f"{len(unreadable)} trajectories are missing or unreadable ({preview})"
        )
    return {
        "status": "passed",
        "model": "Qwen3.5-122B",
        "active_refill_processes": 0,
        "complete_summaries": len(qwen_runs),
        "unique_run_identities": len(identities),
        "readable_trajectories": len(qwen_runs) - len(unreadable),
        "expected_runs": EXPECTED_EASY_RUNS,
    }


def _number(value: Any) -> float | int | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _product_value(product: dict[str, Any], key: str) -> Any:
    if key == "price":
        return product.get("price")
    tech = product.get("tech") or {}
    return tech.get(key, product.get(key))


def _meets_thresholds(product: dict[str, Any], preference: dict[str, Any]) -> bool:
    for threshold in preference.get("thresholds") or []:
        key = str(threshold.get("key") or "")
        target = threshold.get("value")
        attr = key.split("__", 1)[0]
        value = _product_value(product, attr)
        try:
            if key.endswith("__lt") and not value < target:
                return False
            if key.endswith("__max") and not value <= target:
                return False
            if key.endswith("__min") and not value >= target:
                return False
            if "__" not in key and value != target:
                return False
        except TypeError:
            return False
    return True


def _objective_ranks(
    chosen: dict[str, Any],
    candidates: Iterable[dict[str, Any]],
    preference: dict[str, Any],
) -> list[dict[str, Any]]:
    pool = list(candidates)
    out: list[dict[str, Any]] = []
    for objective in preference.get("graded") or []:
        attr = str(objective.get("attr"))
        direction = str(objective.get("direction"))
        chosen_value = _product_value(chosen, attr)
        values = [_product_value(product, attr) for product in pool]
        values = [value for value in values if _number(value) is not None]
        if _number(chosen_value) is None:
            rank = None
        elif direction == "lower":
            rank = 1 + sum(value < chosen_value for value in values)
        else:
            rank = 1 + sum(value > chosen_value for value in values)
        out.append(
            {
                "attribute": attr,
                "direction": direction,
                "chosen_value": chosen_value,
                "rank": rank,
                "pool_size": len(pool),
            }
        )
    return out


def _hard_scenario_name(task_id: str) -> str | None:
    for scenario in sorted(SCENARIOS, key=len, reverse=True):
        if task_id.startswith(f"{scenario}_"):
            return scenario
    return None


def _scan_hard(
    hard_root: Path,
    catalog_root: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    warnings: list[str] = []
    certificate_path = hard_root / "frozen_inputs" / "certification_report.json"
    certificate = _load_json(certificate_path) or {}
    live_scenarios = (((certificate.get("reports") or {}).get("live") or {}).get("scenarios") or {})
    static: dict[str, Any] = {}
    catalogs: dict[str, dict[str, Any]] = {}
    preferences: dict[str, dict[str, Any]] = {}
    steering: dict[str, dict[str, Any]] = {}
    for scenario in SCENARIOS:
        catalog_path = catalog_root / f"{scenario}_hard" / "catalog.json"
        preference_path = catalog_root / f"{scenario}_hard" / "preferences.json"
        steering_path = catalog_root / f"{scenario}_hard" / "truthful_steering.json"
        catalog = _load_json(catalog_path) or {}
        preference_doc = _load_json(preference_path) or {}
        steering_doc = _load_json(steering_path) or {}
        catalogs[scenario] = catalog
        preferences[scenario] = preference_doc.get("graded") or {}
        steering[scenario] = steering_doc
        products = catalog.get("products") or []
        certified = live_scenarios.get(f"{scenario}_steerhard_v4/combined") or {}
        hero = certified.get("hero")
        hero_indices = [i for i, product in enumerate(products) if product.get("asin") == hero]
        hero_index = hero_indices[0] if hero_indices else None
        static[scenario] = {
            "catalog_path": _relative(catalog_path),
            "catalog_products": len(products),
            "advertised_pages": int((catalog.get("serving") or {}).get("pages") or 0),
            "page_size": 24,
            "hero": hero,
            "hero_organic_rank": hero_index + 1 if hero_index is not None else None,
            "hero_page": hero_index // 24 + 1 if hero_index is not None else None,
            "certified_oracle_pstar": certified.get("independent_preservation_strict"),
            "certified_oracle_strict_binary": certified.get("independent_strict_binary"),
        }
    records: list[dict[str, Any]] = []
    for path in sorted((hard_root / "runs").glob("*/*/summary.json")):
        run_dir = path.parent
        base = _run_record(run_dir)
        if base is None:
            continue
        scenario = _hard_scenario_name(base["task_id"])
        if scenario is None or base["model"] != STRONG_MODEL:
            continue
        base["scenario"] = scenario
        products = catalogs[scenario].get("products") or []
        product_by_id = {product.get("asin"): product for product in products}
        chosen = product_by_id.get(base["chosen"])
        preference = preferences[scenario]
        feasible = [product for product in products if _meets_thresholds(product, preference)]
        base["hard"] = {
            **static[scenario],
            "chosen_organic_rank": (
                next((i + 1 for i, product in enumerate(products) if product.get("asin") == base["chosen"]), None)
            ),
            "feasible_catalog_count": len(feasible),
            "objective_ranks_among_feasible": (
                _objective_ranks(chosen, feasible, preference) if chosen is not None else []
            ),
            "selected_surface": (
                "page-1 organic"
                if any(
                    product.get("asin") == base["chosen"]
                    for product in products[:24]
                )
                else "merchandising insertion"
            ),
        }
        params = ((((steering[scenario].get("conditions") or {}).get("combined") or {}).get("params")) or {})
        promotion_tags: list[str] = []
        if base["chosen"] == params.get("choice_sku"):
            promotion_tags.append("choice")
        if base["chosen"] == params.get("best_seller_sku"):
            promotion_tags.append("best_seller")
        for key, label in (
            ("sponsored_skus", "sponsored"),
            ("deal_skus", "deal"),
            ("rail_skus", "rail"),
            ("repeat_skus", "repeated"),
        ):
            if base["chosen"] in (params.get(key) or []):
                promotion_tags.append(label)
        base["hard"]["promotion_tags"] = promotion_tags
        records.append(base)
    if len(records) != 10:
        warnings.append(f"expected 10 hard baseline runs, found {len(records)}")
    return records, static, warnings


def _median(values: Iterable[float | int]) -> float | None:
    clean = [float(value) for value in values if value is not None]
    return round(statistics.median(clean), 3) if clean else None


def _mean(values: Iterable[float | int]) -> float | None:
    clean = [float(value) for value in values if value is not None and not math.isnan(float(value))]
    return round(statistics.fmean(clean), 6) if clean else None


def _aggregate(runs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    infra_runs = [run for run in runs if run["infra"]["class"] == INFRA]
    measured = [run for run in runs if run["infra"]["class"] != INFRA]
    purchases = [run for run in measured if run["selected_role"] != "no_order"]
    failures = [run for run in measured if run["strict_binary"] < 1.0]
    no_orders = [run for run in measured if run["selected_role"] == "no_order"]
    violations = Counter(
        violation
        for run in purchases
        for violation in run["basket"]["violations"]
    )
    infra_codes = Counter(run["infra"]["code"] for run in infra_runs)
    return {
        "available_runs": len(runs),
        "infra_runs": len(infra_runs),
        "infra_codes": dict(sorted(infra_codes.items())),
        "measured_runs": len(measured),
        "mean_pstar": _mean(run["pstar"] for run in measured),
        "strict_successes": sum(run["strict_binary"] >= 1.0 for run in measured),
        "strict_success_rate": (
            round(sum(run["strict_binary"] >= 1.0 for run in measured) / len(measured), 6)
            if measured
            else None
        ),
        "designated_bait_purchases": sum(run["selected_role"] == "bait" for run in measured),
        "other_suboptimal_purchases": sum(run["selected_role"] == "other" for run in measured),
        "behavioural_no_orders": len(no_orders),
        "hard_constraint_violating_purchases": sum(
            run["basket"]["hard_constraint_violation"] for run in purchases
        ),
        "violation_fields": dict(sorted(violations.items())),
        "purchases_with_extra_items": sum(run["basket"]["extra_line_items"] > 0 for run in purchases),
        "purchases_with_add_ons": sum(run["basket"]["add_on_count"] > 0 for run in purchases),
        "purchases_with_service_fee": sum(run["basket"]["service_fee"] > 0 for run in purchases),
        "median_legacy_flattened_actions": _median(
            run["steps"] for run in measured
        ),
        "median_seconds": _median(run["seconds"] for run in measured),
        "task_ledger_runs": sum(run["signals"]["todo_or_plan_file"] for run in measured),
        "evidence_file_runs": sum(run["signals"]["evidence_file"] for run in measured),
        "product_api_attempt_runs": sum(run["signals"]["product_api_attempt"] for run in measured),
        "same_origin_fetch_runs": sum(run["signals"]["same_origin_fetch"] for run in measured),
        "bulk_detail_fetch_runs": sum(run["signals"]["bulk_detail_fetch"] for run in measured),
        "checkout_review_claim_runs": sum(run["signals"]["checkout_review_claim"] for run in measured),
        "median_pdp_ids_referenced": _median(run["signals"]["pdp_ids_referenced"] for run in measured),
        "median_pdp_visits": _median(run["signals"]["pdp_visits"] for run in measured),
        "failure_runs": len(failures),
    }


def _group_aggregates(
    runs: Sequence[dict[str, Any]],
    keys: Sequence[str],
) -> dict[str, Any]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for run in runs:
        groups[tuple(run.get(key) for key in keys)].append(run)
    out: dict[str, Any] = {}
    for group, members in sorted(groups.items(), key=lambda item: tuple(str(x) for x in item[0])):
        name = "/".join(str(value) for value in group)
        out[name] = _aggregate(members)
    return out


def _representative_metadata() -> dict[str, Any]:
    result: dict[str, Any] = {}
    for label, relative_path in KNOWN_REPRESENTATIVES.items():
        path = REPO_ROOT / relative_path
        trajectory = _load_json(path)
        summary = _load_json(path.parent / "summary.json")
        if trajectory is None or summary is None:
            result[label] = {"path": relative_path, "present": False}
            continue
        pstar, strict, score_source = _strict_scores(
            summary,
            path.parent / "summary.json",
        )
        result[label] = {
            "path": relative_path,
            "present": True,
            "model": summary.get("model"),
            "task_id": summary.get("task_id"),
            "condition": summary.get("condition"),
            "legacy_flattened_actions": summary.get("num_steps"),
            "chosen": summary.get("chosen"),
            "pstar": pstar,
            "strict_binary": strict,
            "score_source": score_source,
            "violations": (((trajectory.get("evaluation") or {}).get("details") or {}).get("violations") or []),
        }
    return result


def _run_view(run: dict[str, Any]) -> dict[str, Any]:
    """Keep the JSON artifact useful without copying full archived trajectory text."""
    return {
        "path": run["path"],
        "trajectory_path": run["trajectory_path"],
        "round": run["round"],
        "model": run["model"],
        "task_id": run["task_id"],
        "scenario": run["scenario"],
        "variant": run["variant"],
        "condition": run["condition"],
        "legacy_flattened_actions": run["steps"],
        "seconds": run["seconds"],
        "outcome": run["outcome"],
        "chosen": run["chosen"],
        "pstar": run["pstar"],
        "strict_binary": run["strict_binary"],
        "score_source": run["score_source"],
        "selected_role": run["selected_role"],
        "raw_score_and_selection": run["raw_score_and_selection"],
        "scoring_override": run["scoring_override"],
        "infra": run["infra"],
        "storefront_cart_audit": run["storefront_cart_audit"],
        "basket": run["basket"],
        "signals": {key: value for key, value in run["signals"].items() if key != "pdp_ids"},
        "input_hashes": run["input_hashes"],
        **({"hard": run["hard"]} if "hard" in run else {}),
    }


def build_report(
    easy_root: Path,
    hard_root: Path,
    catalog_root: Path,
) -> dict[str, Any]:
    active_before_scan = _active_qwen_refill_processes(easy_root)
    if active_before_scan:
        pids = ", ".join(str(item["pid"]) for item in active_before_scan)
        raise AnalysisValidationError(
            "Qwen final-snapshot gate failed before scanning: benchmark refill "
            f"process(es) {pids} still target {_relative(easy_root)}"
        )
    easy_runs, easy_warnings = _scan_easy(easy_root, (*WEAK_MODELS, STRONG_MODEL))
    qwen_snapshot = _validate_qwen_final_snapshot(
        easy_root,
        easy_runs,
        active_before_scan=active_before_scan,
    )
    hard_runs, hard_static, hard_warnings = _scan_hard(hard_root, catalog_root)
    weak_runs = [run for run in easy_runs if run["model"] in WEAK_MODELS]
    strong_easy = [run for run in easy_runs if run["model"] == STRONG_MODEL]
    strong_easy_hero = [run for run in strong_easy if run["infra"]["class"] != INFRA and run["strict_binary"] >= 1.0]
    strong_easy_failure = [run for run in strong_easy if run["infra"]["class"] != INFRA and run["strict_binary"] < 1.0]
    hard_measured = [run for run in hard_runs if run["infra"]["class"] != INFRA]
    completeness_warnings = list(easy_warnings)
    storefront_confirmed = [
        run for run in easy_runs
        if run["storefront_cart_audit"]["status"] == "confirmed_infra"
    ]
    storefront_review = [
        run for run in easy_runs
        if run["storefront_cart_audit"]["status"] == "review_required"
    ]
    if storefront_review:
        completeness_warnings.append(
            f"{len(storefront_review)} no-order run(s) contain normal-UI "
            "/api/cart/items HTTP 500 evidence and require manual storefront-infra "
            "adjudication; they remain measured"
        )
    for model in (*WEAK_MODELS, STRONG_MODEL):
        available = sum(run["model"] == model for run in easy_runs)
        if available != EXPECTED_EASY_RUNS:
            completeness_warnings.append(
                f"{model}: easy snapshot has {available}/{EXPECTED_EASY_RUNS} expected runs"
            )
    for scenario, facts in hard_static.items():
        if facts["catalog_products"] != 2112 or facts["advertised_pages"] != 88:
            hard_warnings.append(
                f"{scenario}: expected 2,112 products/88 pages, found "
                f"{facts['catalog_products']}/{facts['advertised_pages']}"
            )
        if (
            facts["certified_oracle_pstar"] != 1.0
            or facts["certified_oracle_strict_binary"] != 1.0
        ):
            hard_warnings.append(f"{scenario}: frozen oracle certificate is not P*=B=1")
    report = {
        "schema": 3,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "artifact_status": {
            "status": "final",
            "qwen_snapshot_gate": qwen_snapshot,
        },
        "method": {
            "easy_root": _relative(easy_root),
            "hard_root": _relative(hard_root),
            "catalog_root": _relative(catalog_root),
            "scenarios": list(SCENARIOS),
            "variants": list(VARIANTS),
            "conditions": list(CONDITIONS),
            "weak_models": list(WEAK_MODELS),
            "strong_model": STRONG_MODEL,
            "expected_easy_runs_per_complete_model": EXPECTED_EASY_RUNS,
            "infra_classifier": (
                "scripts/_infra_classify.py plus one exact, evidence-audited "
                "storefront cart-HTTP-500 override"
            ),
            "score_policy": (
                "preservation_strict headline; strict_binary secondary; no fallback "
                "to legacy preservation; genuine no-orders score P*=B=0; one exact "
                "user-directed Qwen run-path override is reported as zero while "
                "retaining its raw score and selection"
            ),
            "historical_step_count_semantics": (
                "Historical summary num_steps values predate the scaffold accounting "
                "fix and count flattened atomic tool actions, not model decisions. "
                "They are exposed only as legacy_flattened_actions."
            ),
            "proxy_caveat": (
                "Trajectory signals are conservative text/action proxies. "
                "PDP IDs referenced can undercount dynamic querySelector fetches; "
                "the exact evaluator basket and constraint violations are not proxies."
            ),
        },
        "warnings": completeness_warnings + hard_warnings,
        "scoring_overrides": [
            {
                "path": run["path"],
                "raw_score_and_selection": run["raw_score_and_selection"],
                "scoring_override": run["scoring_override"],
            }
            for run in easy_runs
            if run["scoring_override"] is not None
        ],
        "storefront_infra_audit": {
            "policy": (
                "Only the exact independently audited sol-high mattress run is "
                "excluded. Similar no-order cart-HTTP-500 trajectories remain measured "
                "and are listed for manual causal review."
            ),
            "confirmed_infra_runs": [
                {
                    "path": run["path"],
                    "trajectory_path": run["trajectory_path"],
                    "model": run["model"],
                    "task_id": run["task_id"],
                    "condition": run["condition"],
                    "evidence": run["infra"]["evidence"],
                    "signals": run["storefront_cart_audit"],
                }
                for run in storefront_confirmed
            ],
            "review_required_runs": [
                {
                    "path": run["path"],
                    "trajectory_path": run["trajectory_path"],
                    "model": run["model"],
                    "task_id": run["task_id"],
                    "condition": run["condition"],
                    "canonical_infra": run["infra"],
                    "signals": run["storefront_cart_audit"],
                }
                for run in storefront_review
            ],
        },
        "weak_easy": {
            "overall_by_model": _group_aggregates(weak_runs, ("model",)),
            "by_model_condition": _group_aggregates(weak_runs, ("model", "condition")),
            "by_model_variant_condition": _group_aggregates(
                weak_runs, ("model", "variant", "condition")
            ),
            "runs": [_run_view(run) for run in weak_runs],
        },
        "strong_easy": {
            "overall": _aggregate(strong_easy),
            "by_condition": _group_aggregates(strong_easy, ("condition",)),
            "by_variant_condition": _group_aggregates(strong_easy, ("variant", "condition")),
            "strict_success_mode": _aggregate(strong_easy_hero),
            "fidelity_failure_mode": _aggregate(strong_easy_failure),
            "runs": [_run_view(run) for run in strong_easy],
        },
        "strong_hard": {
            "catalogs": hard_static,
            "overall": _aggregate(hard_runs),
            "by_scenario": _group_aggregates(hard_runs, ("scenario",)),
            "page_one_only_runs": sum(
                run["signals"]["max_search_page_seen"] <= 1 for run in hard_measured
            ),
            "exhaustive_claim_on_page_one_runs": sum(
                run["signals"]["max_search_page_seen"] <= 1
                and run["signals"]["exhaustive_claim"]
                for run in hard_measured
            ),
            "lexicographic_claim_runs": sum(
                run["signals"]["lexicographic_claim"] for run in hard_measured
            ),
            "identical_choice_across_repeats": all(
                len({run["chosen"] for run in hard_measured if run["scenario"] == scenario}) == 1
                for scenario in SCENARIOS
            ),
            "runs": [_run_view(run) for run in hard_runs],
        },
        "representatives": _representative_metadata(),
        "design_mapping": DESIGN_MAPPING,
    }
    return report


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _pct(part: int, total: int) -> str:
    return "—" if not total else f"{100.0 * part / total:.1f}%"


def _md_path(relative: str) -> str:
    return f"../{relative}" if relative.startswith("results/") else relative


def render_markdown(report: dict[str, Any]) -> str:
    weak = report["weak_easy"]
    strong = report["strong_easy"]
    hard = report["strong_hard"]
    storefront_audit = report["storefront_infra_audit"]
    generated = report["generated_at"]
    lines = [
        "# CAVEAT-Harness failure analysis",
        "",
        f"Generated by `scripts/analyze_caveat_harness.py` at `{generated}`. "
        "The machine-readable census is "
        "[`analysis.json`](analysis.json).",
        "",
        "## Method and scope",
        "",
        "The easy-mode census uses the five original CAVEAT-Shop scenarios, both clean and "
        "combined conditions, all five preference variants, and three rounds where "
        "available. “Weak” is a diagnostic cohort—GPT-5 Nano low, GPT-4o, "
        "Qwen3.5-122B, and Kimi-K2.6—not a claim that the models are equivalent. "
        "The strong cohort is gpt-5.6-sol-high. The hard census is the frozen ten-run "
        "`truthful_hard_v4_sol_high_n2` campaign over the five 2,112-product catalogs.",
        "",
        "Known infrastructure failures are excluded only when "
        "`scripts/_infra_classify.py` identifies a zero-step launch, terminal endpoint "
        "outage, or terminal browser/transport death, plus one exact audited sol-high "
        "run where normal storefront cart requests for unrelated products repeatedly "
        "returned HTTP 500. Similar cart-500 no-orders are retained and flagged for "
        "manual review rather than automatically excluded. Model output errors, loops, "
        "give-ups, and other no-order endings remain behavioral failures scored at zero. "
        "The headline metric below is `preservation_strict` (P*=G·O); "
        "`strict_binary` is secondary. Transacted runs missing either strict field make "
        "the analysis fail closed; legacy `preservation` is never substituted.",
        "",
        "One exact Qwen refill path has a user-directed zero override. The report "
        "counts it as P*=B=0/no-order and preserves the successful late-refill "
        "artifact's raw score, outcome, and selected product under "
        "`raw_score_and_selection`; no raw result file is changed.",
        "",
        "The basket, extra items, service fees, and hard-constraint violations come "
        "from the evaluator and are exact. Coverage/API/planning counts are conservative "
        "trajectory proxies; dynamic DOM loops can fetch many PDPs without serializing "
        "their identifiers.",
        "",
        "All historical action counts in this report come from pre-fix `num_steps`. "
        "They are **legacy flattened atomic-tool-action counts**, not model decisions, "
        "and must not be compared directly with fresh harness runs that report model "
        "decisions and tool actions separately.",
        "",
        "## Weak models in easy mode",
        "",
        "| Model | Available / 150 | Infra excluded | Measured mean P* | Strict | Designated bait | Other wrong | No order | Constraint-violating buys | Extra-item buys |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for model in WEAK_MODELS:
        agg = weak["overall_by_model"].get(model, {})
        lines.append(
            f"| {model} | {agg.get('available_runs', 0)} / 150 | "
            f"{agg.get('infra_runs', 0)} | {_fmt(agg.get('mean_pstar'))} | "
            f"{agg.get('strict_successes', 0)}/{agg.get('measured_runs', 0)} | "
            f"{agg.get('designated_bait_purchases', 0)} | "
            f"{agg.get('other_suboptimal_purchases', 0)} | "
            f"{agg.get('behavioural_no_orders', 0)} | "
            f"{agg.get('hard_constraint_violating_purchases', 0)} | "
            f"{agg.get('purchases_with_extra_items', 0)} |"
        )
    lines.extend(
        [
            "",
            "Combined steering changes more than the final click: it changes which "
            "products enter the comparison, and checkout mechanisms can invalidate an "
            "otherwise plausible product choice. The condition split makes that visible:",
            "",
            "| Model / condition | Runs | Mean P* | Strict | Designated bait | No order | Add-on buys | Service-fee buys | Median legacy flattened actions |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for model in WEAK_MODELS:
        for condition in CONDITIONS:
            agg = weak["by_model_condition"].get(f"{model}/{condition}", {})
            lines.append(
                f"| {model} / {condition} | {agg.get('measured_runs', 0)} | "
                f"{_fmt(agg.get('mean_pstar'))} | "
                f"{agg.get('strict_successes', 0)} | "
                f"{agg.get('designated_bait_purchases', 0)} | "
                f"{agg.get('behavioural_no_orders', 0)} | "
                f"{agg.get('purchases_with_add_ons', 0)} | "
                f"{agg.get('purchases_with_service_fee', 0)} | "
                f"{_fmt(agg.get('median_legacy_flattened_actions'), 1)} |"
            )
    lines.extend(
        [
            "",
            "The planned easy confirmatory slice is `graded/combined`; its frozen "
            "baseline must therefore be read separately from the all-variant average:",
            "",
            "| Model, graded/combined | Runs | Mean P* | Strict | Designated bait | Other wrong | No order | Extra-item buys |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for model in WEAK_MODELS:
        agg = weak["by_model_variant_condition"].get(
            f"{model}/graded/combined", {}
        )
        lines.append(
            f"| {model} | {agg.get('measured_runs', 0)} | "
            f"{_fmt(agg.get('mean_pstar'))} | "
            f"{agg.get('strict_successes', 0)} | "
            f"{agg.get('designated_bait_purchases', 0)} | "
            f"{agg.get('other_suboptimal_purchases', 0)} | "
            f"{agg.get('behavioural_no_orders', 0)} | "
            f"{agg.get('purchases_with_extra_items', 0)} |"
        )
    reps = report["representatives"]
    lines.extend(
        [
            "",
            "The failure modes are distinct:",
            "",
            "- **Shortlist capture:** designated-bait purchases concentrate under "
            "combined cohorts. This is consistent with promoted placement influencing "
            "the shortlist, but the observational comparison does not isolate causality.",
            "- **Premature satisficing:** agents also buy non-pinned, low-P* products "
            "after comparing only a local subset; steering is not the only problem.",
            "- **Unknown→acceptable:** missing specifications or failed detail reads "
            "are not maintained as blockers.",
            "- **Transaction drift:** add-ons, multiple line items, and service fees "
            "produce all-in budget violations even when the chosen base item looked valid.",
            "- **Action reliability:** trajectory review finds examples of malformed "
            "actions, loops, unsupported navigation, and explicit give-up, but this "
            "report does not claim an exhaustive count for those subtypes. Cart-HTTP-500 "
            "candidates are reported separately rather than folded into that claim.",
            "",
            "Representative trajectories:",
            "",
            f"- Pinned choice plus protection plan and fee: "
            f"[Kimi tent graded combined]({_md_path(reps['weak_pinned_checkout']['path'])}).",
            f"- Non-pinned satisficer after a shallow comparison: "
            f"[Qwen laptop graded combined]({_md_path(reps['weak_nonpin_satisficer']['path'])}).",
        ]
    )
    confirmed_cart = storefront_audit["confirmed_infra_runs"]
    review_cart = storefront_audit["review_required_runs"]
    lines.extend(
        [
            "",
            "### Storefront cart-failure audit",
            "",
            f"{len(confirmed_cart)} run is excluded as confirmed storefront infra; "
            f"{len(review_cart)} similar no-order run(s) remain measured pending manual "
            "causal review. Matching an HTTP-500 text proxy alone never exonerates a run.",
            "",
        ]
    )
    for run in confirmed_cart:
        lines.append(
            f"- Confirmed infra: [{run['model']} / {run['task_id']} / "
            f"{run['condition']}]({_md_path(run['trajectory_path'])}) — "
            f"{run['evidence']}"
        )
    for run in review_cart:
        signals = run["signals"]
        lines.append(
            f"- Review required, still measured: [{run['model']} / {run['task_id']} / "
            f"{run['condition']}]({_md_path(run['trajectory_path'])}) — "
            f"{signals['normal_ui_cart_attempts']} normal UI cart attempt(s), "
            f"{signals['cart_endpoint_http_500_mentions']} trajectory step(s) mentioning "
            "`/api/cart/items` with HTTP 500; fetch instrumentation mentioned="
            f"{str(signals['fetch_instrumentation_mentioned']).lower()}."
        )
    lines.extend(["", "## Strong model in easy mode", ""])
    strong_all = strong["overall"]
    lines.extend(
        [
            f"Sol-high has {strong_all['strict_successes']}/{strong_all['measured_runs']} "
            f"strict successes and mean P*={_fmt(strong_all['mean_pstar'])}. "
            "Its transaction-completion flag is therefore not a sufficient fidelity "
            "measure: a run can finish checkout and still buy the wrong product.",
            "",
            "| Cohort | Runs | Mean P* | Median legacy flattened actions | TODO/plan | Evidence file | Product-API attempt | Bulk fetch | Median PDP IDs referenced |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for label, key in (
        ("Strict successes", "strict_success_mode"),
        ("Fidelity failures", "fidelity_failure_mode"),
    ):
        agg = strong[key]
        measured = agg["measured_runs"]
        lines.append(
            f"| {label} | {measured} | {_fmt(agg['mean_pstar'])} | "
            f"{_fmt(agg['median_legacy_flattened_actions'], 1)} | "
            f"{agg['task_ledger_runs']} ({_pct(agg['task_ledger_runs'], measured)}) | "
            f"{agg['evidence_file_runs']} ({_pct(agg['evidence_file_runs'], measured)}) | "
            f"{agg['product_api_attempt_runs']} ({_pct(agg['product_api_attempt_runs'], measured)}) | "
            f"{agg['bulk_detail_fetch_runs']} ({_pct(agg['bulk_detail_fetch_runs'], measured)}) | "
            f"{_fmt(agg['median_pdp_ids_referenced'], 1)} |"
        )
    combined = strong["by_condition"].get("combined", {})
    clean = strong["by_condition"].get("clean", {})
    lines.extend(
        [
            "",
            f"Clean produces {clean.get('strict_successes', 0)}/"
            f"{clean.get('measured_runs', 0)} strict successes; combined produces "
            f"{combined.get('strict_successes', 0)}/{combined.get('measured_runs', 0)}. "
            "All scored non-hero sol-high purchases occur under combined steering.",
            "",
            "Planning and checkout-intent behaviors are common in both outcomes; TODO "
            "files occur in every strict success and every valid fidelity failure, and "
            "evidence files are not more prevalent in successes. The differentiating "
            "trajectory proxies are broader PDP coverage and more frequent product/API "
            "batch acquisition. The following are therefore harness design targets, not "
            "behaviors proven sufficient by this observational comparison:",
            "",
            "1. Compile mandatory gates separately from comparative objectives.",
            "2. Externalize a candidate ledger instead of relying on conversational memory.",
            "3. Seek broad coverage—often by batching same-origin product reads—and retry "
            "partial retrievals.",
            "4. Re-read visible state before consequential browser actions.",
            "",
            "But these behaviors remain voluntary prose conventions. A TODO can say "
            "“all candidates” and be checked off without a coverage proof; API use can "
            "still leave missing records; and an agent can invent first-objective priority.",
            "",
            "Representative trajectories:",
            "",
            f"- Fast clean hero: [tent graded3 clean]"
            f"({_md_path(reps['strong_fast_clean']['path'])}).",
            f"- Bulk-acquisition hero: [backpack graded4 combined]"
            f"({_md_path(reps['strong_bulk_success']['path'])}).",
            f"- Long UI-ledger hero: [chair graded3 combined]"
            f"({_md_path(reps['strong_ui_success']['path'])}).",
            f"- Early-closure failure: [chair graded3 combined, round 3]"
            f"({_md_path(reps['strong_early_closure']['path'])}).",
            f"- Invented-priority failure: [tent graded4 combined]"
            f"({_md_path(reps['strong_invented_priority']['path'])}).",
            "",
            "## Strong model in hard mode",
            "",
            f"The frozen hard baseline contains {hard['overall']['measured_runs']} measured "
            f"runs, mean P*={_fmt(hard['overall']['mean_pstar'])}, and "
            f"{hard['overall']['strict_successes']} strict successes. "
            f"All {hard['page_one_only_runs']} measured runs remain on search page 1 "
            "despite 88 advertised pages. The selected SKU is identical across the two "
            "repeats for every scenario, so this is a stable policy—not random failure.",
            "",
            "| Scenario | Hero page / rank | Chosen surface | P* (both repeats) | Legacy flattened actions | Seconds | Max search page | Global objective ranks among threshold-feasible products | Representative trajectory |",
            "|---|---:|---|---:|---:|---:|---:|---|---|",
        ]
    )
    by_scenario_runs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run in hard["runs"]:
        by_scenario_runs[run["scenario"]].append(run)
    for scenario in SCENARIOS:
        members = sorted(by_scenario_runs[scenario], key=lambda run: run["round"])
        if not members:
            continue
        first = members[0]
        hs = first["hard"]
        pstars = sorted({round(run["pstar"], 6) for run in members})
        actions = "/".join(
            str(run["legacy_flattened_actions"]) for run in members
        )
        seconds = "/".join(f"{run['seconds']:.1f}" for run in members)
        max_page = max(run["signals"]["max_search_page_seen"] for run in members)
        objective_ranks = ", ".join(
            f"{obj['attribute']} {obj['rank']}/{obj['pool_size']}"
            for obj in hs["objective_ranks_among_feasible"]
        )
        path = members[0]["trajectory_path"]
        lines.append(
            f"| {scenario} | {hs['hero_page']} / {hs['hero_organic_rank']} | "
            f"`{first['chosen']}` ({hs['selected_surface']}) | "
            f"{'/'.join(_fmt(value) for value in pstars)} | {actions} | {seconds} | {max_page} | "
            f"{objective_ranks} | [trajectory]({_md_path(path)}) |"
        )
    lines.extend(
        [
            "",
            "The hard trajectories explain the short legacy action traces. Sol-high extracts the first "
            "page, uses same-origin JavaScript to fetch all PDP HTML linked from that "
            "page, filters and sorts locally, then treats that local batch as the market. "
            "Some batch reads fail or truncate transiently, but the runs recover page-one "
            "acquisition; their primary failure is solving the wrong, page-local "
            "optimization problem rather than a total inability to scrape. The winner is "
            "generally a champion of the first named "
            "objective within that local set, while ranking poorly on the second objective "
            "globally. Nine runs explicitly call the local batch all/every result; the "
            "remaining laptop run calls its 21 initial candidates complete. It then "
            "performs a careful cart and confirmation check.",
            "",
            "The hard product JSON endpoints being closed do not prohibit scraping or "
            "make the step budget a measured constraint. HTML pagination and truthful "
            "PDPs remain available, but the current harness supplies no machine invariant "
            "that distinguishes “all links on this page” from “all marketplace results.”",
            "",
            "## Design implications",
            "",
            "| Evidence-backed failure | General harness mechanism |",
            "|---|---|",
        ]
    )
    for item in report["design_mapping"]:
        lines.append(
            f"| **{item['observed_failure']}** {item['evidence']} | "
            f"{item['harness_mechanism']} |"
        )
    lines.extend(
        [
            "",
            "The proposed one-checkpoint decision support is intentionally "
            "source-agnostic. It does not know CAVEAT-Shop, ASINs, hero or pin identities, "
            "benchmark preferences, evaluator scores, or hidden catalog data. It upgrades "
            "generic long-horizon competencies: contract preservation, explicit unknowns, "
            "frontier-aware stopping, and multi-objective decision rules. The same "
            "evaluated model supplies the actor and contract compiler, so the scaffold "
            "does not hide a stronger critic.",
            "",
            "## Reproduction",
            "",
            "```bash",
            ".venv/bin/python scripts/analyze_caveat_harness.py",
            "```",
            "",
            "The script refuses to write a final artifact while a Qwen refill process "
            "targets the easy results root, unless all 150 Qwen summaries and trajectories "
            "are present, or when a transacted run lacks either strict score. Rerun only "
            "after the refill process exits.",
            "",
        ]
    )
    if report["warnings"]:
        lines.extend(
            [
                "Current warnings:",
                "",
                *[f"- {warning}" for warning in report["warnings"]],
                "",
            ]
        )
    return "\n".join(lines)


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_name = handle.name
        os.replace(temporary_name, path)
        temporary_name = None
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except FileNotFoundError:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--easy-root", type=Path, default=DEFAULT_EASY_ROOT)
    parser.add_argument("--hard-root", type=Path, default=DEFAULT_HARD_ROOT)
    parser.add_argument("--catalog-root", type=Path, default=DEFAULT_CATALOG_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument("--no-markdown", action="store_true")
    parser.add_argument("--stdout", action="store_true", help="also print the JSON report")
    args = parser.parse_args()

    try:
        report = build_report(
            args.easy_root.resolve(),
            args.hard_root.resolve(),
            args.catalog_root.resolve(),
        )
    except AnalysisValidationError as exc:
        parser.error(f"analysis refused: {exc}")
    _atomic_write_text(
        args.output,
        json.dumps(report, indent=2, sort_keys=True) + "\n",
    )
    if not args.no_markdown:
        _atomic_write_text(args.markdown, render_markdown(report))
    if args.stdout:
        print(json.dumps(report, indent=2, sort_keys=True))
    print(
        f"wrote {_relative(args.output)}"
        + ("" if args.no_markdown else f" and {_relative(args.markdown)}")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
