#!/usr/bin/env python
"""Fail-closed report for the exact CAVEAT truthful-hard sol-high campaign.

The reporter discovers only manifest-named run paths.  It never produces a
headline mean from a partial denominator.  A run is excluded and marked for a
recoverable refill if it touched any harness backstop, switched to browser-use's
fallback LLM, exhausted SDK retries, truncated model output, terminated with an
infrastructure error, or lacks fresh strict metrics.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import statistics
import sys
from collections import Counter
from pathlib import Path


def _discover_root() -> Path:
    configured = os.environ.get("PREFERENCE_FIDELITY_ROOT")
    candidates = [
        Path(configured) if configured else None,
        Path.cwd(),
        Path(__file__).resolve().parents[1],
    ]
    for candidate in candidates:
        if (
            candidate
            and (candidate / "caveat").is_dir()
            and (candidate / "scripts").is_dir()
        ):
            return candidate.resolve()
    raise SystemExit("cannot locate preference-fidelity root")


ROOT = _discover_root()
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(ROOT / "scripts"))

from freeze_hard_campaign import (  # noqa: E402
    CONDITION,
    EVENT_TIMEOUT_NAMES,
    MODEL_RECORDED,
    REGIONS,
    SCENARIOS,
    SUCCESS_THRESHOLD,
    verify_campaign,
)


CONFOUND_PATTERNS = {
    "fallback_switch": (
        r"Primary LLM \([^)]*\) failed with .*switching to fallback LLM",
        r"switching to fallback LLM \([^)]*\)",
    ),
    "sdk_retry_exhaustion": (
        r"CAVEAT_LLM_SDK_RETRIES_EXHAUSTED",
        r"\bmax(?:imum)? retries exceeded\b",
        r"\bretr(?:y|ies|y attempts?).{0,100}"
        r"(?:exhausted|exceeded|gave up|giving up)\b",
    ),
    "model_output_truncation": (
        r"\bModelOutputTruncated(?:Error)?\b",
        r"finish_reason\s*[:=]\s*['\"]?length\b",
        r"finish reason.{0,60}\blength\b",
    ),
}

HARNESS_PATTERNS = {
    "llm_timeout": (
        r"\bLLM\b.{0,120}\b(?:timed out|TimeoutError)\b",
        r"\b(?:chat|responses?) (?:completion )?(?:request|call)\b"
        r".{0,120}\b(?:timed out|timeout)\b",
    ),
    "llm_http_timeout": (
        r"\b(?:APITimeoutError|httpx\.(?:Read|Connect|Write|Pool)Timeout)\b",
    ),
    "step_timeout": (
        r"\bstep(?:_timeout| timeout|.{0,30}timed out)\b",
        r"TimeoutError:.{0,100}\bstep\b",
    ),
    "extract_llm_timeout": (
        r"CAVEAT_EXTRACT_LLM_TIMEOUT_BOUND",
        r"\bextract(?:ion)?\b.{0,160}\b(?:timed out|TimeoutError)\b",
    ),
    "event_handler_timeout": (
        r"Error in event handler.{0,500}(?:TimeoutError|timed out)",
        r"EventBus.{0,500}(?:TimeoutError|timed out)",
    ),
    "navigation_timeout": (
        r"\bPage\.navigate\(\)\s+timed out\b",
        r"\bNavigateToUrlEvent.{0,500}(?:TimeoutError|timed out)",
    ),
    "cdp_timeout": (
        r"\bCDP (?:method|request)\b.{0,200}"
        r"(?:did not respond within|timed out)",
    ),
    "browser_action_timeout": (
        r"\bper-action timeout\b",
        r"\bAction\b.{0,160}\btimed out after\s+\d+(?:\.\d+)?s\b",
    ),
    "max_failures_stop": (
        r"Stopping due to \d+ consecutive failures",
        r"(?:maximum|max) (?:consecutive )?failures.{0,80}"
        r"(?:reached|exceeded|stop)",
    ),
    "cell_timeout": (
        r"\bCAVEAT_CELL_TIMEOUT_BOUND\b",
        r"\bcell(?:_timeout| timeout)\b",
    ),
    "clickable_elements_truncation": (
        r"Interactive elements \(truncated to 40000 characters\)",
    ),
    "read_or_action_state_truncation": (
        r"\[Content truncated at 60k characters\]",
    ),
    "evaluate_output_truncation": (
        r"\[Truncated after (?:20000|67108864) characters\]",
        r"\bCAVEAT_EVALUATE_(?:SINGLE_BOUND|STORE_"
        r"INTEGRITY_FAILURE)\b",
    ),
    "extract_chunk_truncation": (
        r"\btruncated_at_char\b",
        r"\bis_partial['\"]?\s*[:=]\s*(?:true|True)\b",
    ),
}

INFRA_PATTERNS = {
    "auth_401_403": (
        r"(?:AuthenticationError|PermissionDeniedError)",
        r"(?:TRAPI|deployment|OpenAI|LLM|responses?).{0,180}\b(?:401|403)\b",
    ),
    "deployment_404": (
        r"(?:deployment|model).{0,180}\b404\b",
        r"NotFoundError.{0,180}(?:deployment|model)",
    ),
    "provider_429": (
        r"(?:RateLimitError|TooManyRequests)",
        r"(?:TRAPI|deployment|OpenAI|LLM|responses?).{0,180}\b429\b",
    ),
    "provider_5xx": (
        r"(?:InternalServerError|BadGateway|ServiceUnavailable)",
        r"(?:TRAPI|deployment|OpenAI|LLM|responses?).{0,180}\b5\d\d\b",
    ),
}


def _read_json(path: Path):
    return json.loads(path.read_text())


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha_json(value) -> str:
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def _clean_text(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _scan(text: str, patterns: dict[str, tuple[str, ...]]) -> dict[str, dict]:
    """Scan line-by-line to avoid a timeout word in model prose joining an error."""
    lines = _clean_text(text).splitlines()
    out = {}
    for label, regexes in patterns.items():
        hits = []
        for line in lines:
            for regex in regexes:
                match = re.search(regex, line, flags=re.I)
                if match:
                    excerpt = re.sub(r"\s+", " ", match.group(0)).strip()[:300]
                    if excerpt and excerpt not in hits:
                        hits.append(excerpt)
        if hits:
            out[label] = {"count": len(hits), "examples": hits[:3]}
    return out


def _scan_event_timeouts(
    text: str,
    event_timeouts: dict[str, int],
) -> dict[str, dict]:
    out = {}
    for name in event_timeouts:
        escaped = re.escape(name)
        hit = _scan(text, {
            name: (
                rf"\b{escaped}\b.{{0,500}}(?:TimeoutError|timed out)",
                rf"(?:TimeoutError|timed out).{{0,300}}\b{escaped}\b",
            )
        }).get(name)
        if hit:
            out[name] = hit
    return out


def _network_work(text: str) -> dict:
    """Extract conservative diagnostics; this is not request telemetry.

    A browser-use ``evaluate`` action can execute arbitrary JS loops and thousands
    of fetches while consuming one recorded browser step.  We therefore report
    logged bulk-work evidence separately and explicitly refuse to equate steps
    with requests.
    """
    clean = _clean_text(text)
    lines = clean.splitlines()
    evaluate_lines = [
        line for line in lines
        if re.search(r"\bevaluate\b.*\bcode\b", line, flags=re.I)
    ]
    fetch_evaluates = [
        line for line in evaluate_lines if re.search(r"\bfetch\s*\(", line)
    ]
    asin_evaluates = [
        line for line in evaluate_lines
        if "/api/products/asin/" in line
    ]
    listing_evaluates = [
        line for line in evaluate_lines
        if "/api/products?" in line
    ]
    listing_estimates = []
    loop_re = re.compile(
        r"for\s*\([^;]*=\s*0\s*;[^;]*<\s*(\d{2,5})\s*;"
        r"[^)]*\+=\s*(\d{1,4})",
        flags=re.I,
    )
    for line in listing_evaluates:
        for total_raw, stride_raw in loop_re.findall(line):
            total, stride = int(total_raw), int(stride_raw)
            if stride > 0:
                listing_estimates.append({
                    "loop_total": total,
                    "page_stride": stride,
                    "implied_listing_requests": math.ceil(total / stride),
                })
    detail_claims = []
    claim_patterns = (
        r"(?:fetch|retrieve|read|load|inspect|compare)\s+(?:all\s+)?"
        r"([\d,]{2,})\s+(?:candidate\s+)?"
        r"(?:detail|product-detail|PDP)(?:\s+records|\s+pages|\s+reads)?",
        r"(?:detail|product-detail|PDP)(?:\s+records|\s+pages|\s+reads)?"
        r"\s+(?:for\s+)?(?:all\s+)?([\d,]{2,})\s+(?:candidate|product)",
        r"([\d,]{2,})\s+(?:candidate\s+)?(?:details|PDPs)\s+"
        r"(?:fetched|retrieved|read|loaded|checked)",
    )
    for line in lines:
        for regex in claim_patterns:
            for raw in re.findall(regex, line, flags=re.I):
                value = int(raw.replace(",", ""))
                if 1 < value <= 100000:
                    detail_claims.append(value)
    concurrency_claims = []
    concurrency_patterns = (
        r"(?:batch(?:es)?\s+of|batch[_ ]?size\s*[:=]?)\s*(\d{1,3})",
        r"(\d{1,3})\s+(?:concurrent\s+)?workers\b",
        r"concurrenc(?:y|t)\s*[:=]?\s*(\d{1,3})",
        r"chunk(?:Size|_size|\s+size)?\s*[:=]\s*(\d{1,3})",
    )
    for line in lines:
        for regex in concurrency_patterns:
            for raw in re.findall(regex, line, flags=re.I):
                value = int(raw)
                if 1 <= value <= 500:
                    concurrency_claims.append(value)
    bulk_markers = {
        "promise_all": len(re.findall(r"\bPromise\.all(?:Settled)?\s*\(", clean)),
        "asin_endpoint_mentions": clean.count("/api/products/asin/"),
        "listing_endpoint_mentions": clean.count("/api/products?"),
        "bulk_language_mentions": len(re.findall(
            r"\b(?:bulk|batched|concurrent|enumerated all|complete catalog)\b",
            clean,
            flags=re.I,
        )),
        "expected_product_json_404_mentions": len(re.findall(
            r"(?:/api/products(?:/asin/[^?\s'\"`]+)?|product JSON)"
            r".{0,120}\b404\b"
            r"|\b404\b.{0,120}/api/products",
            clean,
            flags=re.I,
        )),
    }
    return {
        "instrumentation": "log-derived conservative diagnostics, not exact telemetry",
        "exact_request_count_available": False,
        "recorded_browser_steps_equal_network_requests": False,
        "evaluate_actions_logged": len(evaluate_lines),
        "evaluate_actions_with_literal_fetch": len(fetch_evaluates),
        "evaluate_actions_with_listing_endpoint": len(listing_evaluates),
        "evaluate_actions_with_asin_detail_endpoint": len(asin_evaluates),
        "literal_fetch_call_sites_logged": len(
            re.findall(r"\bfetch\s*\(", clean)
        ),
        "listing_loop_estimates": listing_estimates,
        "max_implied_listing_requests": max(
            (
                record["implied_listing_requests"]
                for record in listing_estimates
            ),
            default=None,
        ),
        "detail_read_claims": sorted(set(detail_claims)),
        "max_detail_read_claim": max(detail_claims, default=None),
        "concurrency_claims": sorted(set(concurrency_claims)),
        "max_concurrency_claim": max(concurrency_claims, default=None),
        "bulk_markers": bulk_markers,
        "bulk_api_attempt_evidence": bool(
            asin_evaluates
            and (
                bulk_markers["promise_all"]
                or bulk_markers["bulk_language_mentions"]
                or detail_claims
            )
        ),
        "transport_evidence": (
            "server_rendered_html_with_product_json_404"
            if bulk_markers["expected_product_json_404_mentions"]
            else "server_rendered_html_manifest_contract"
        ),
        "interpretation": (
            "One evaluate action can issue many requests; browser steps are not "
            "a measure of enumeration cost."
        ),
    }


def _catalog_info(campaign_dir: Path, manifest: dict) -> dict[str, dict]:
    frozen_root = campaign_dir / manifest["frozen_artifact_root"]
    out = {}
    for scenario in SCENARIOS:
        pool = _read_json(frozen_root / scenario / "pool.json")
        by_asin = {row["asin"]: row for row in pool}
        record = manifest["artifacts"][scenario]
        out[scenario] = {
            "by_asin": by_asin,
            "asins": set(by_asin),
            "hero": record["hero_asin"],
            "low_primary": record["low_primary_asin"],
            "choice_frontier": record["choice_frontier_asin"],
        }
    return out


def _purchase_class(chosen: str | None, info: dict) -> str:
    if not chosen:
        return "other"
    if chosen == info["hero"]:
        return "hero"
    if chosen == info["low_primary"]:
        return "low_primary"
    if chosen == info["choice_frontier"]:
        return "choice_frontier"
    return "other"


def _numeric(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _empty_row(spec: dict, path: Path, reason: str) -> dict:
    return {
        **{
            key: spec[key] for key in (
                "run_id", "block", "spawn_index", "scenario", "variant",
                "condition", "primary_region", "region_order", "run_name",
                "summary_relpath",
            )
        },
        "path": str(path),
        "summary_present": False,
        "model": None,
        "outcome": None,
        "chosen": None,
        "chosen_label": None,
        "purchase_class": "other",
        "preservation_strict": None,
        "strict_binary": None,
        "steps": 0,
        "seconds": 0.0,
        "no_bound": False,
        "backstop_evidence": {},
        "confound_signatures": {},
        "harness_signatures": {},
        "event_timeout_signatures": {},
        "infra_signatures": {},
        "network_work": {
            "exact_request_count_available": False,
            "recorded_browser_steps_equal_network_requests": False,
            "interpretation": (
                "No run log was available; steps still cannot be treated as "
                "network-request counts."
            ),
        },
        "field_errors": [reason],
        "aggregate_eligible": False,
        "aggregate_exclusion_reasons": [reason],
        "error": reason,
    }


def _row(
    campaign_dir: Path,
    spec: dict,
    info: dict,
    caps: dict,
) -> dict:
    summary_path = campaign_dir / spec["summary_relpath"]
    if not summary_path.is_file():
        return _empty_row(spec, summary_path, "expected summary is missing")
    try:
        summary = _read_json(summary_path)
    except Exception as exc:  # noqa: BLE001
        return _empty_row(
            spec, summary_path, f"summary JSON is unreadable: {exc}"
        )
    texts = []
    for relpath in (
        spec["run_log_relpath"],
        spec["launcher_log_relpath"],
    ):
        path = campaign_dir / relpath
        if path.is_file():
            texts.append(path.read_text(errors="replace"))
    error = str(summary.get("error") or "")
    scan_text = "\n".join(texts) + "\nSUMMARY_ERROR: " + error
    confounds = _scan(scan_text, CONFOUND_PATTERNS)
    harness = _scan(scan_text, HARNESS_PATTERNS)
    event_timeouts = _scan_event_timeouts(
        scan_text, caps["event_timeouts_seconds"]
    )
    infra = _scan(scan_text, INFRA_PATTERNS)
    field_errors = []
    try:
        steps = int(summary.get("num_steps"))
        if steps < 0:
            raise ValueError
    except (TypeError, ValueError):
        steps = 0
        field_errors.append("num_steps is not a non-negative integer")
    try:
        seconds = float(summary.get("seconds"))
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError
    except (TypeError, ValueError):
        seconds = 0.0
        field_errors.append("seconds is not a finite non-negative number")
    step_bound = steps >= int(caps["max_steps"])
    time_bound = seconds >= float(caps["cell_timeout_seconds"]) * 0.99
    expected = {
        "task_id": f"{spec['scenario']}-{spec['variant']}",
        "condition": CONDITION,
        "model": MODEL_RECORDED,
        "scaffold": "browseruse",
        "env": "caveat_shop",
    }
    for key, wanted in expected.items():
        if summary.get(key) != wanted:
            field_errors.append(
                f"{key}={summary.get(key)!r}, expected {wanted!r}"
            )
    for key in ("preservation_strict", "strict_binary"):
        if key not in summary:
            field_errors.append(f"fresh summary lacks {key}")
        elif not _numeric(summary.get(key)):
            field_errors.append(f"{key} is not a finite number")
    if _numeric(summary.get("preservation_strict")) and not (
        0 <= float(summary["preservation_strict"]) <= 1
    ):
        field_errors.append("preservation_strict is outside [0,1]")
    if _numeric(summary.get("strict_binary")) and float(
        summary["strict_binary"]
    ) not in {0.0, 1.0}:
        field_errors.append("strict_binary is not 0 or 1")
    chosen = summary.get("chosen")
    if chosen and chosen not in info["asins"]:
        field_errors.append("chosen ASIN is outside the frozen catalog")
    outcome = summary.get("outcome")
    terminal_failure = (
        outcome in {None, "error", "skipped"} or bool(error)
    )
    backstop_touched = (
        step_bound
        or time_bound
        or bool(harness)
        or bool(event_timeouts)
    )
    confound_touched = bool(confounds)
    reasons = list(field_errors)
    if terminal_failure:
        reasons.append("terminal run/infrastructure failure")
    if confound_touched:
        reasons.append(
            "fallback/retry-exhaustion/model-truncation confound touched"
        )
    if backstop_touched:
        reasons.append("harness/backstop ceiling touched")
    no_bound = not backstop_touched and not confound_touched
    return {
        **{
            key: spec[key] for key in (
                "run_id", "block", "spawn_index", "scenario", "variant",
                "condition", "primary_region", "region_order", "run_name",
                "summary_relpath",
            )
        },
        "path": str(summary_path),
        "summary_present": True,
        "model": summary.get("model"),
        "outcome": outcome,
        "chosen": chosen,
        "chosen_label": summary.get("chosen_label"),
        "purchase_class": _purchase_class(chosen, info),
        "preservation_strict": (
            float(summary["preservation_strict"])
            if _numeric(summary.get("preservation_strict")) else None
        ),
        "strict_binary": (
            float(summary["strict_binary"])
            if _numeric(summary.get("strict_binary")) else None
        ),
        "steps": steps,
        "seconds": seconds,
        "step_utilization": round(steps / caps["max_steps"], 8),
        "time_utilization": round(
            seconds / caps["cell_timeout_seconds"], 8
        ),
        "step_backstop_bound": step_bound,
        "time_backstop_bound": time_bound,
        "no_bound": no_bound,
        "backstop_evidence": {
            "step_cap": caps["max_steps"],
            "observed_steps": steps,
            "step_headroom": caps["max_steps"] - steps,
            "step_utilization": round(steps / caps["max_steps"], 8),
            "cell_timeout_seconds": caps["cell_timeout_seconds"],
            "observed_seconds": seconds,
            "time_headroom_seconds": round(
                caps["cell_timeout_seconds"] - seconds, 1
            ),
            "time_utilization": round(
                seconds / caps["cell_timeout_seconds"], 8
            ),
            "confound_signatures": sorted(confounds),
            "harness_signatures": sorted(harness),
            "event_timeout_signatures": sorted(event_timeouts),
            "all_untouched": no_bound,
        },
        "confound_signatures": confounds,
        "harness_signatures": harness,
        "event_timeout_signatures": event_timeouts,
        "infra_signatures": infra,
        "network_work": _network_work(scan_text),
        "field_errors": field_errors,
        "aggregate_eligible": not reasons,
        "aggregate_exclusion_reasons": reasons,
        "error": error or None,
    }


def _ceiling_audit(rows: list[dict], caps: dict) -> dict:
    events = caps["event_timeouts_seconds"]
    if set(events) != set(EVENT_TIMEOUT_NAMES):
        raise ValueError("incomplete event-timeout inventory")

    def ids(predicate) -> list[str]:
        return sorted(row["run_id"] for row in rows if predicate(row))

    def harness(label: str):
        return lambda row: label in row.get("harness_signatures", {})

    def confound(label: str):
        return lambda row: label in row.get("confound_signatures", {})

    core = {
        "max_steps": {
            "configured": caps["max_steps"],
            "source": "benchmark.run --max-steps / Agent.run(max_steps)",
            "touched_runs": ids(
                lambda row: row.get("step_backstop_bound") is True
            ),
        },
        "cell_timeout_seconds": {
            "configured": caps["cell_timeout_seconds"],
            "source": "CAVEAT_CELL_TIMEOUT / asyncio.wait_for",
            "touched_runs": ids(
                lambda row: row.get("time_backstop_bound") is True
                or harness("cell_timeout")(row)
            ),
        },
        "llm_timeout_seconds": {
            "configured": caps["llm_timeout_seconds"],
            "source": "CAVEAT_LLM_TIMEOUT / Agent.llm_timeout",
            "touched_runs": ids(harness("llm_timeout")),
        },
        "llm_http_timeout_seconds": {
            "configured": caps["llm_http_timeout_seconds"],
            "source": "ChatOpenAI timeout",
            "touched_runs": ids(harness("llm_http_timeout")),
        },
        "step_timeout_seconds": {
            "configured": caps["step_timeout_seconds"],
            "source": "Agent.step_timeout",
            "touched_runs": ids(harness("step_timeout")),
        },
        "extract_llm_timeout_seconds": {
            "configured": caps["extract_llm_timeout_seconds"],
            "source": "Browser Use extract timeout patch",
            "touched_runs": ids(harness("extract_llm_timeout")),
        },
        "cdp_request_timeout_seconds": {
            "configured": caps["cdp_request_timeout_seconds"],
            "source": "BROWSER_USE_CDP_TIMEOUT_S",
            "touched_runs": ids(harness("cdp_timeout")),
        },
        "browser_action_timeout_seconds": {
            "configured": caps["browser_action_timeout_seconds"],
            "source": "BROWSER_USE_ACTION_TIMEOUT_S",
            "touched_runs": ids(harness("browser_action_timeout")),
        },
        "max_consecutive_failures": {
            "configured": caps["max_consecutive_failures"],
            "source": "CAVEAT_MAX_FAILURES / Agent.max_failures",
            "touched_runs": ids(harness("max_failures_stop")),
        },
        "max_completion_tokens": {
            "configured": caps["max_completion_tokens"],
            "source": "CAVEAT_MAX_COMPLETION_TOKENS",
            "touched_runs": ids(confound("model_output_truncation")),
        },
        "llm_sdk_max_retries": {
            "configured": caps["llm_sdk_max_retries"],
            "source": "ChatOpenAI max_retries",
            "touched_runs": ids(confound("sdk_retry_exhaustion")),
        },
        "fallback_llm_depth": {
            "configured": 1,
            "source": (
                "runtime_dependencies.agent_behavior_limits."
                "fallback_llm_depth"
            ),
            "touched_runs": ids(confound("fallback_switch")),
        },
        "max_clickable_elements_chars": {
            "configured": 40000,
            "source": (
                "runtime_dependencies.effective_limit_audit.context_limits."
                "max_clickable_elements_chars"
            ),
            "touched_runs": ids(harness("clickable_elements_truncation")),
        },
        "read_state_and_action_results_chars": {
            "configured": 60000,
            "source": (
                "runtime_dependencies.effective_limit_audit.context_limits."
                "read_state_chars/action_results_chars"
            ),
            "touched_runs": ids(harness("read_or_action_state_truncation")),
        },
        "evaluate_output_chars": {
            "configured": 20000,
            "source": (
                "runtime_dependencies.effective_limit_audit.context_limits."
                "evaluate_output_chars"
            ),
            "touched_runs": ids(harness("evaluate_output_truncation")),
        },
        "extract_page_chunk_chars": {
            "configured": 100000,
            "source": (
                "runtime_dependencies.effective_limit_audit.context_limits."
                "extract_page_chunk_chars"
            ),
            "touched_runs": ids(harness("extract_chunk_truncation")),
        },
    }
    event_audit = {}
    for name, seconds in events.items():
        event_audit[name] = {
            "configured_seconds": seconds,
            "source": f"TIMEOUT_{name}",
            "touched_runs": ids(
                lambda row, event=name: (
                    event in row.get("event_timeout_signatures", {})
                    or (
                        event == "NavigateToUrlEvent"
                        and "navigation_timeout"
                        in row.get("harness_signatures", {})
                    )
                )
            ),
        }
    touched = [
        name for name, record in core.items() if record["touched_runs"]
    ] + [
        f"event:{name}"
        for name, record in event_audit.items()
        if record["touched_runs"]
    ]
    return {
        "inventory_complete": True,
        "core_ceiling_count": len(core),
        "event_ceiling_count": len(event_audit),
        "total_ceiling_count": len(core) + len(event_audit),
        "all_ceilings_and_fallback_untouched": not touched,
        "touched_names": touched,
        "core": core,
        "event_timeouts": event_audit,
    }


def _safe_campaign_file(
    campaign_dir: Path,
    raw: dict,
    label: str,
    errors: list[str],
) -> Path | None:
    try:
        path = (campaign_dir / raw["path"]).resolve()
    except Exception as exc:  # noqa: BLE001
        errors.append(f"{label}: malformed file reference: {exc}")
        return None
    root = campaign_dir.resolve()
    if path != root and root not in path.parents:
        errors.append(f"{label}: referenced file escapes campaign")
        return None
    if not path.is_file():
        errors.append(f"{label}: referenced file missing")
        return None
    if _sha_file(path) != raw.get("sha256"):
        errors.append(f"{label}: referenced file hash mismatch")
        return None
    return path


def _parse_concurrency_log(path: Path) -> dict | None:
    text = path.read_text(errors="replace")
    marker = "===== MAX SAFE CONCURRENCY (>=90% ok) ====="
    try:
        tail = text.split(marker, 1)[1]
        start = tail.index("{")
        summary, _ = json.JSONDecoder().raw_decode(tail[start:])
        return summary["gpt-5.6-sol"]
    except Exception:
        return None


def _probe_errors(
    campaign_dir: Path,
    manifest: dict,
    receipts: dict[str, dict],
) -> list[str]:
    errors = []
    required = {
        "before_block1": 1,
        "mid_before_block2": 2,
    }
    checkpoints = {}
    probe_root = campaign_dir / "probes"
    for path in probe_root.glob("checkpoint_*.json") if probe_root.exists() else []:
        try:
            checkpoint = _read_json(path)
            checkpoints[checkpoint.get("checkpoint")] = (path, checkpoint)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{path.name}: unreadable checkpoint: {exc}")
    for label, block in required.items():
        if label not in checkpoints:
            errors.append(f"{label}: required probe checkpoint missing")
            continue
        checkpoint = checkpoints[label][1]
        if checkpoint.get("launch_scope") != {"block": block}:
            errors.append(f"{label}: launch scope mismatch")
        block_rows = [
            row for row in manifest["schedule"] if row["block"] == block
        ]
        expected_load = dict(Counter(
            row["primary_region"] for row in block_rows
        ))
        if checkpoint.get("scheduled_primary_load") != expected_load:
            errors.append(f"{label}: scheduled primary load mismatch")
    for label, (_, checkpoint) in checkpoints.items():
        if not isinstance(label, str):
            errors.append("checkpoint has no string label")
            continue
        if set(checkpoint.get("healthy_regions") or []) != set(REGIONS):
            errors.append(f"{label}: all three regions were not healthy")
        capacity = checkpoint.get("concurrency_capacity")
        load = checkpoint.get("scheduled_primary_load")
        if (
            not isinstance(capacity, dict)
            or set(capacity) != set(REGIONS)
            or not isinstance(load, dict)
            or not set(load).issubset(set(REGIONS))
            or any(
                type(capacity[region]) is not int
                or capacity[region] < load.get(region, 0)
                for region in REGIONS
            )
        ):
            errors.append(f"{label}: concurrency capacity does not cover load")
        large = _safe_campaign_file(
            campaign_dir,
            checkpoint.get("large_log") or {},
            f"{label}/large",
            errors,
        )
        concurrency = _safe_campaign_file(
            campaign_dir,
            checkpoint.get("concurrency_log") or {},
            f"{label}/concurrency",
            errors,
        )
        small = _safe_campaign_file(
            campaign_dir,
            checkpoint.get("small_log") or {},
            f"{label}/small",
            errors,
        )
        if small is not None and not small.read_text(errors="replace").strip():
            errors.append(f"{label}: small probe log is empty")
        if large is not None:
            lines = [
                line.strip()
                for line in large.read_text(errors="replace").splitlines()
                if line.strip()
            ]
            try:
                served = set(json.loads(lines[-1]))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{label}: invalid large-probe JSON: {exc}")
            else:
                if served != set(REGIONS):
                    errors.append(f"{label}: large probe did not pass all regions")
        if concurrency is not None:
            measured = _parse_concurrency_log(concurrency)
            if measured != capacity:
                errors.append(f"{label}: concurrency checkpoint/log mismatch")
    for run_id, receipt in receipts.items():
        label = receipt.get("probe_checkpoint")
        if label not in checkpoints:
            errors.append(f"{run_id}: launch probe checkpoint missing")
            continue
        try:
            published = dt.datetime.fromisoformat(
                checkpoints[label][1]["published_at_utc"].replace("Z", "+00:00")
            )
            launched = dt.datetime.fromisoformat(
                receipt["launched_at_utc"].replace("Z", "+00:00")
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{run_id}: invalid probe/launch timestamp: {exc}")
        else:
            if published > launched:
                errors.append(f"{run_id}: run launched before its probe passed")
    return errors


def _receipt_errors(
    campaign_dir: Path,
    manifest: dict,
) -> tuple[list[str], list[str], dict[str, dict]]:
    errors = []
    unexpected = []
    receipts = {}
    policy = manifest["runtime_environment_policy"]
    root = campaign_dir / "launch_receipts"
    expected = {row["run_id"]: row for row in manifest["schedule"]}
    found_paths = list(root.glob("*.json")) if root.exists() else []
    for path in found_paths:
        run_id = path.stem
        if run_id not in expected:
            unexpected.append(str(path))
            continue
        try:
            receipt = _read_json(path)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{run_id}: unreadable launch receipt: {exc}")
            continue
        receipts[run_id] = receipt
        spec = expected[run_id]
        wanted = {
            "run_id": run_id,
            "block": spec["block"],
            "scenario": spec["scenario"],
            "condition": spec["condition"],
            "port": spec["port"],
            "primary_region": spec["primary_region"],
            "region_order": spec["region_order"],
        }
        for key, value in wanted.items():
            if receipt.get(key) != value:
                errors.append(
                    f"{run_id}: receipt {key}={receipt.get(key)!r}, "
                    f"expected {value!r}"
                )
        if type(receipt.get("attempt")) is not int or receipt["attempt"] < 1:
            errors.append(f"{run_id}: invalid attempt number")
        runtime = receipt.get("runtime_contract") or {}
        per_run = set(policy["per_run_set"])
        expected_absent = [
            name for name in policy["required_absent_after_apply"]
            if name not in per_run
        ]
        region_json = json.dumps(
            {"gpt-5.6-sol": spec["region_order"]},
            separators=(",", ":"),
        )
        contract = {
            "caps": manifest["caps"],
            "runtime_dependencies_sha256": manifest[
                "runtime_dependencies"
            ]["sha256"],
            "runtime_environment_policy_sha256": policy["sha256"],
            "source_inventory_sha256": manifest["source_inventory_sha256"],
            "frozen_artifact_inventory_sha256": manifest[
                "frozen_artifact_inventory_sha256"
            ],
            "certification_sha256": manifest[
                "certification"
            ]["frozen_sha256"],
        }
        wanted_runtime = {
            "caps": manifest["caps"],
            "runtime_dependencies_sha256": manifest[
                "runtime_dependencies"
            ]["sha256"],
            "runtime_environment_policy_sha256": policy["sha256"],
            "source_inventory_sha256": manifest["source_inventory_sha256"],
            "frozen_artifact_inventory_sha256": manifest[
                "frozen_artifact_inventory_sha256"
            ],
            "certification_sha256": manifest[
                "certification"
            ]["frozen_sha256"],
            "observed_set": policy["set"],
            "observed_absent": expected_absent,
            "sanitized_namespace_leaks": [],
            "trapi_regions_override": region_json,
            "cli_max_steps": manifest["caps"]["max_steps"],
            "contract_sha256": _sha_json(contract),
        }
        for key, value in wanted_runtime.items():
            if runtime.get(key) != value:
                errors.append(f"{run_id}: runtime receipt {key} mismatch")
    return errors, unexpected, receipts


def _blocked_report(campaign_dir: Path, reason: str) -> dict:
    return {
        "schema_version": 1,
        "kind": "hard_sol_high_campaign_report",
        "campaign_id": campaign_dir.name,
        "status": "invalid",
        "status_reason": reason,
        "headline_metric": "preservation_strict",
        "headline_formula": "P*=G*O",
        "secondary_metric": "strict_binary",
        "legacy_preservation_used": False,
        "headline_result": {
            "available": False,
            "expected_n": 10,
            "mean_preservation_strict": None,
            "mean_strict_binary": None,
            "unavailable_reason": reason,
        },
        "validity": {
            "green": False,
            "complete": False,
            "refillable": False,
            "refill_run_ids": [],
            "integrity_error": reason,
            "aggregation_attempted": False,
        },
        "runs": [],
    }


def build_report(campaign_dir: Path) -> dict:
    try:
        manifest = verify_campaign(campaign_dir, quiet=True)
    except (SystemExit, ValueError) as exc:
        return _blocked_report(
            campaign_dir,
            f"campaign freeze/cert/source/artifact integrity failure: {exc}",
        )
    catalog = _catalog_info(campaign_dir, manifest)
    rows = [
        _row(
            campaign_dir,
            spec,
            catalog[spec["scenario"]],
            manifest["caps"],
        )
        for spec in manifest["schedule"]
    ]
    expected_paths = {
        (campaign_dir / spec["summary_relpath"]).resolve()
        for spec in manifest["schedule"]
    }
    found_paths = {
        path.resolve()
        for path in (campaign_dir / "runs").rglob("summary.json")
    } if (campaign_dir / "runs").is_dir() else set()
    missing = sorted(str(path) for path in expected_paths - found_paths)
    unexpected = sorted(str(path) for path in found_paths - expected_paths)
    receipt_errors, unexpected_receipts, receipts = _receipt_errors(
        campaign_dir, manifest
    )
    probe_errors = _probe_errors(campaign_dir, manifest, receipts)
    ceiling_audit = _ceiling_audit(rows, manifest["caps"])
    refill_ids = sorted(
        row["run_id"] for row in rows if not row["aggregate_eligible"]
    )
    matrix = Counter((row["scenario"], row["condition"]) for row in rows)
    exact_matrix = (
        len(rows) == 10
        and matrix == Counter({
            (scenario, CONDITION): 2 for scenario in SCENARIOS
        })
    )
    all_eligible = all(row["aggregate_eligible"] for row in rows)
    all_receipts = set(receipts) == {row["run_id"] for row in rows}
    evidence_green = (
        not missing
        and not unexpected
        and not receipt_errors
        and not unexpected_receipts
        and not probe_errors
        and exact_matrix
        and all_eligible
        and all_receipts
        and ceiling_audit["all_ceilings_and_fallback_untouched"]
    )
    values = [
        row["preservation_strict"]
        for row in rows if row["aggregate_eligible"]
    ]
    binaries = [
        row["strict_binary"]
        for row in rows if row["aggregate_eligible"]
    ]
    complete_denominator = evidence_green and len(values) == len(binaries) == 10
    mean_pstar = statistics.fmean(values) if complete_denominator else None
    mean_binary = statistics.fmean(binaries) if complete_denominator else None
    behavior_pass = (
        mean_pstar is not None and mean_pstar < SUCCESS_THRESHOLD
    )
    if complete_denominator:
        status = "pass" if behavior_pass else "fail"
        reason = (
            "valid complete denominator and mean preservation_strict < 0.30"
            if behavior_pass
            else "valid complete denominator but mean preservation_strict >= 0.30"
        )
    else:
        structural = bool(unexpected or unexpected_receipts or not exact_matrix)
        status = "invalid" if structural else "incomplete"
        reason = (
            "unexpected result/receipt contamination or schedule-matrix drift"
            if structural
            else "one or more expected runs/evidence are missing or excluded"
        )
    refillable = (
        status == "incomplete"
        and not unexpected
        and not unexpected_receipts
        and not probe_errors
        and exact_matrix
        and bool(refill_ids)
    )
    purchase_counts = Counter(
        row["purchase_class"] for row in rows if row["aggregate_eligible"]
    )
    network_summary = {
        "exact_request_telemetry_available": False,
        "steps_are_not_request_counts": True,
        "runs_with_bulk_api_attempt_evidence": sum(
            bool(row["network_work"].get("bulk_api_attempt_evidence"))
            for row in rows
        ),
        "expected_product_json_404_mentions": sum(
            (
                row["network_work"].get("bulk_markers", {}).get(
                    "expected_product_json_404_mentions", 0
                )
            )
            for row in rows
        ),
        "max_log_claimed_detail_reads": max(
            (
                row["network_work"].get("max_detail_read_claim") or 0
                for row in rows
            ),
            default=0,
        ) or None,
        "max_log_implied_listing_requests": max(
            (
                row["network_work"].get("max_implied_listing_requests") or 0
                for row in rows
            ),
            default=0,
        ) or None,
        "interpretation": (
            "Recorded browser steps undercount network work: one evaluate step "
            "can issue thousands of requests. Network fields are conservative "
            "log diagnostics, not exact telemetry."
        ),
    }
    validity = {
        "green": evidence_green,
        "complete": complete_denominator,
        "refillable": refillable,
        "refill_run_ids": refill_ids if refillable else [],
        "integrity_error": None,
        "missing_expected_summaries": missing,
        "unexpected_summaries": unexpected,
        "receipt_errors": receipt_errors,
        "unexpected_receipts": unexpected_receipts,
        "probe_errors": probe_errors,
        "excluded_runs": {
            row["run_id"]: row["aggregate_exclusion_reasons"]
            for row in rows if not row["aggregate_eligible"]
        },
        "exact_five_scenarios_by_two_runs_matrix": exact_matrix,
        "exact_denominator_complete": complete_denominator,
        "all_frozen_ceilings_and_fallback_untouched": ceiling_audit[
            "all_ceilings_and_fallback_untouched"
        ],
        "aggregation_attempted": True,
    }
    caps = manifest["caps"]
    return {
        "schema_version": 1,
        "kind": "hard_sol_high_campaign_report",
        "campaign_id": manifest["campaign_id"],
        "status": status,
        "status_reason": reason,
        "headline_metric": "preservation_strict",
        "headline_formula": "P*=G*O",
        "secondary_metric": "strict_binary",
        "legacy_preservation_used": False,
        "success_threshold": {
            "operator": "<",
            "value": SUCCESS_THRESHOLD,
        },
        "headline_result": {
            "available": complete_denominator,
            "condition": CONDITION,
            "expected_n": 10,
            "finite_n": len(values),
            "excluded_n": len(refill_ids),
            "mean_preservation_strict": mean_pstar,
            "mean_strict_binary": mean_binary,
            "unavailable_reason": None if complete_denominator else reason,
        },
        "purchase_class_counts": dict(sorted(purchase_counts.items())),
        "validity": validity,
        "backstops": {
            "effective_caps": caps,
            "ceiling_audit": ceiling_audit,
            "max_step_utilization": max(
                (row.get("step_utilization", 0) for row in rows),
                default=0,
            ),
            "max_time_utilization": max(
                (row.get("time_utilization", 0) for row in rows),
                default=0,
            ),
            "no_bound_runs": [
                row["run_id"] for row in rows if row["no_bound"]
            ],
            "bound_or_confound_runs": [
                row["run_id"] for row in rows if not row["no_bound"]
            ],
            "agent_behavior_limits": manifest[
                "runtime_dependencies"
            ]["agent_behavior_limits"],
            "effective_limit_audit": manifest[
                "runtime_dependencies"
            ]["effective_limit_audit"],
            "cap_bearing_dependency_sources": manifest[
                "runtime_dependencies"
            ]["cap_bearing_dependency_sources"],
            "by_model": {
                MODEL_RECORDED: {
                    "runs": 10,
                    "caps": caps,
                    "ceiling_audit": ceiling_audit,
                }
            },
        },
        "network_work_summary": network_summary,
        "transport": {
            **manifest["design"]["transport"],
            "observed_json_404_policy": (
                "Expected /api/products JSON 404s are design evidence, not "
                "infrastructure-failure signatures."
            ),
        },
        "manifest_sha256": _read_json(
            campaign_dir / "campaign_manifest.json.sha256"
        )["sha256"],
        "runs": rows,
    }


def print_report(report: dict) -> None:
    if not report["validity"]["aggregation_attempted"]:
        print("=== truthful-hard sol-high campaign report blocked ===")
        print(f"STATUS: {report['status'].upper()}")
        print(f"REASON: {report['status_reason']}")
        print("HEADLINE: unavailable")
        return
    print("=== truthful-hard sol-high per-run results ===")
    print(
        "block scenario class P* binary steps duration_s no_bound "
        "bulk_api chosen"
    )
    for row in report["runs"]:
        pstar = (
            "NA" if row["preservation_strict"] is None
            else f"{row['preservation_strict']:.4f}"
        )
        binary = (
            "NA" if row["strict_binary"] is None
            else f"{row['strict_binary']:.0f}"
        )
        print(
            f"{row['block']} {row['scenario']} {row['purchase_class']} "
            f"{pstar} {binary} {row['steps']} {row['seconds']:.1f} "
            f"{str(row['no_bound']).lower()} "
            f"{str(bool(row['network_work'].get('bulk_api_attempt_evidence'))).lower()} "
            f"{row['chosen'] or '-'}"
        )
    headline = report["headline_result"]
    print("=== headline ===")
    if headline["available"]:
        print(
            f"combined n=10 mean preservation_strict="
            f"{headline['mean_preservation_strict']:.4f}; "
            f"mean strict_binary={headline['mean_strict_binary']:.3f}"
        )
    else:
        print(f"UNAVAILABLE: {headline['unavailable_reason']}")
    backstops = report["backstops"]
    print(
        f"backstops: max step utilization="
        f"{backstops['max_step_utilization']:.2%}; "
        f"max time utilization={backstops['max_time_utilization']:.2%}; "
        f"bound/confounded={len(backstops['bound_or_confound_runs'])}; "
        f"inventory={backstops['ceiling_audit']['total_ceiling_count']} "
        "effective core/event/fallback ceilings"
    )
    network = report["network_work_summary"]
    print(
        "network work: exact request telemetry unavailable; "
        f"bulk-API attempt evidence in "
        f"{network['runs_with_bulk_api_attempt_evidence']}/10 "
        "runs; "
        f"expected product-JSON 404 mentions="
        f"{network['expected_product_json_404_mentions']}; "
        "recorded browser steps are not request counts"
    )
    if report["validity"]["refillable"]:
        print(
            "REFILL REQUIRED: "
            + ", ".join(report["validity"]["refill_run_ids"])
        )
    print(f"VALIDITY: {'GREEN' if report['validity']['green'] else 'RED'}")
    print(f"STATUS: {report['status'].upper()}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("campaign_dir", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    campaign_dir = args.campaign_dir.resolve()
    report = build_report(campaign_dir)
    print_report(report)
    output = (args.json or campaign_dir / "report.json").resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"report: {output}")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
