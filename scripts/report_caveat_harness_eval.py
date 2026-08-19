#!/usr/bin/env python
"""Fail-closed scorer and per-run report for the CAVEAT-Harness A/B.

The reporter discovers only manifest-named paths, rescoring each completed run
with strict P*=G*O.  It never computes an A/B headline from a partial or
confounded paired denominator, and it treats scenario clusters—not repeated
runs—as the independent units for inferential statistics.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import itertools
import json
import math
import os
import random
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from _infra_classify import INFRA, classify_run  # noqa: E402
from caveat_harness_eval_campaign import (  # noqa: E402
    CAVEAT_HARNESS_DIAGNOSTIC_FIELDS,
    HARD_SCENARIOS,
    KIND,
    _contract_diagnostic_error,
    reject_abandoned,
    validate_action_error_partition,
    verify_launch_receipt,
    verify_campaign,
    verify_smoke_gate,
)
from hard_campaign_runtime import (  # noqa: E402
    applicable_limit_inventory,
    runtime_limit_contract,
    validate_limit_audit,
)
from report_hard_campaign import (  # noqa: E402
    CONFOUND_PATTERNS,
    HARNESS_PATTERNS,
    _scan,
    _scan_event_timeouts,
)

CONTEXT_CAP_NAMES = (
    "action_error_chars",
    "action_results_chars",
    "evaluate_memory_chars",
    "extract_already_collected_items",
    "extract_memory_chars",
    "extract_page_chunk_chars",
    "max_clickable_elements_chars",
    "read_state_chars",
)


def _utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _read_json(path: Path):
    return json.loads(path.read_text())


def _write_new_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, sort_keys=True) + "\n"
    try:
        with path.open("x") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise SystemExit(
            f"refusing to replace create-only report file: {path}"
        ) from exc


def _write_new_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise SystemExit(
            f"refusing to replace create-only report file: {path}"
        ) from exc


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _numeric(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _rescore(manifest: dict, campaign_dir: Path) -> dict:
    from caveat.scoring.rescore import write_strict
    started = _utcnow()
    updated = 0
    for relpath in dict.fromkeys(
        row["experiment_relpath"] for row in manifest["schedule"]
    ):
        experiment = campaign_dir / relpath
        if experiment.is_dir():
            updated += write_strict(str(experiment))
    required_keys = {
        "preservation_strict",
        "preservation_cont",
        "strict_binary",
        "resistance_margin",
    }
    outputs = []
    for row in manifest["schedule"]:
        summary_path = campaign_dir / row["summary_relpath"]
        trajectory_path = campaign_dir / row["trajectory_relpath"]
        if not summary_path.is_file() or not trajectory_path.is_file():
            continue
        try:
            summary = _read_json(summary_path)
        except Exception:  # noqa: BLE001
            continue
        present = sorted(required_keys.intersection(summary))
        outputs.append({
            "run_id": row["run_id"],
            "summary_sha256": _sha_file(summary_path),
            "trajectory_sha256": _sha_file(trajectory_path),
            "strict_keys_present": present,
            "strict_values": {
                key: summary.get(key) for key in sorted(required_keys)
            },
        })
    scorer_path = ROOT / "caveat" / "scoring" / "rescore.py"
    exact_run_ids = (
        len(outputs) == len(manifest["schedule"])
        and [record["run_id"] for record in outputs]
        == [row["run_id"] for row in manifest["schedule"]]
    )
    complete = (
        updated == len(manifest["schedule"])
        and exact_run_ids
        and all(
            set(record["strict_keys_present"]) == required_keys
            for record in outputs
        )
    )
    return {
        "kind": "fresh_strict_rescore_audit",
        "performed": True,
        "started_at_utc": started,
        "completed_at_utc": _utcnow(),
        "scorer": {
            "path": str(scorer_path.relative_to(ROOT)),
            "sha256": _sha_file(scorer_path),
        },
        "expected_summary_count": len(manifest["schedule"]),
        "updated_summary_count": updated,
        "output_count": len(outputs),
        "required_strict_keys": sorted(required_keys),
        "fresh_complete": complete,
        "outputs": outputs,
    }


def _no_rescore_audit(manifest: dict) -> dict:
    return {
        "kind": "fresh_strict_rescore_audit",
        "performed": False,
        "started_at_utc": None,
        "completed_at_utc": None,
        "scorer": None,
        "expected_summary_count": len(manifest["schedule"]),
        "updated_summary_count": 0,
        "output_count": 0,
        "required_strict_keys": [
            "preservation_cont",
            "preservation_strict",
            "resistance_margin",
            "strict_binary",
        ],
        "fresh_complete": False,
        "outputs": [],
    }


def _catalog_roles(campaign_dir: Path, manifest: dict) -> dict[str, dict]:
    frozen = campaign_dir / manifest["frozen_artifact_root"]
    out = {}
    for scenario in manifest["artifacts"]:
        pool = _read_json(frozen / scenario / "pool.json")
        asins = {row["asin"] for row in pool}
        heroes = {
            row["asin"] for row in pool
            if row.get("decoy_kind") == "hero"
        }
        if len(heroes) != 1:
            raise ValueError(f"{scenario}: frozen pool lacks one exact hero")
        low_primary = None
        choice = None
        if scenario in HARD_SCENARIOS:
            steering = _read_json(
                frozen / scenario / "truthful_steering.json"
            )
            combined = steering["conditions"]["combined"]
            pins = set(combined.get("decoy_skus") or [])
            params = combined.get("params") or {}
            low_primary = params.get("best_seller_sku")
            choice = params.get("choice_sku")
        else:
            steering = _read_json(frozen / scenario / "steering.json")
            combined = steering.get("combined", steering)
            pins = set(combined.get("decoy_skus") or [])
        out[scenario] = {
            "asins": asins,
            "hero": next(iter(heroes)),
            "pins": pins,
            "low_primary": low_primary,
            "choice": choice,
        }
    return out


def _purchase_role(chosen: str | None, info: dict) -> str:
    if not chosen:
        return "no_order"
    if chosen not in info["asins"]:
        return "off_catalog"
    if chosen == info["hero"]:
        return "hero"
    if chosen == info.get("low_primary"):
        return "pin_low_primary"
    if chosen == info.get("choice"):
        return "pin_choice_frontier"
    if chosen in info["pins"]:
        return "pinned_other"
    return "other"


def _caveat_harness_diagnostics(trajectory: dict) -> dict:
    stats = trajectory.get("stats") or {}
    source = stats.get("caveat_harness")
    if not isinstance(source, dict):
        source = {}
    missing = [
        name for name in CAVEAT_HARNESS_DIAGNOSTIC_FIELDS
        if name not in source
    ]
    malformed = []
    integer_fields = (
        "contract_compile_calls",
        "constraint_count",
        "objective_count",
        "decision_checkpoint_calls",
        "decision_checkpoint_rejections",
        "decision_checkpoint_approvals",
        "checkpoint_candidate_count",
        "frontier_inspected_count",
        "frontier_advertised_count",
        "frontier_advertised_page_count",
        "frontier_enumerated_page_count",
        "structured_max_attempts_observed",
        "structured_attempt_exhaustions",
        "auxiliary_calls",
        "auxiliary_tokens",
    )
    for name in integer_fields:
        if name in source and (
            type(source[name]) is not int or source[name] < 0
        ):
            malformed.append(name)
    if (
        source.get("contract_compile_calls") != 1
        or (
            type(source.get("decision_checkpoint_calls")) is int
            and type(source.get("decision_checkpoint_rejections")) is int
            and type(source.get("decision_checkpoint_approvals")) is int
            and (
                source["decision_checkpoint_approvals"]
                + source["decision_checkpoint_rejections"]
                != source["decision_checkpoint_calls"]
            )
        )
        or (
            type(source.get("structured_max_attempts_observed")) is int
            and source["structured_max_attempts_observed"] > 4
        )
        or (
            type(source.get("structured_attempt_exhaustions")) is int
            and source["structured_attempt_exhaustions"] not in {0, 1}
        )
        or (
            source.get("decision_checkpoint_calls") == 0
            and source.get("frontier_coverage_mode") is not None
        )
        or (
            isinstance(source.get("decision_checkpoint_calls"), int)
            and source["decision_checkpoint_calls"] > 0
            and source.get("frontier_coverage_mode")
            not in {"advertised_total", "finite_pages"}
        )
    ):
        malformed.append("counter_relationships")
    contract_sha256 = source.get("contract_sha256")
    if (
        "contract_sha256" in source
        and (
            not isinstance(contract_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", contract_sha256) is None
        )
    ):
        malformed.append("contract_sha256")
    if (
        "contract_canonical_json" in source
        and _contract_diagnostic_error(source) is not None
    ):
        malformed.append("contract_canonical_json")
    if (
        "search_mode" in source
        and source.get("search_mode") not in {
            "best_available", "satisfice",
        }
    ):
        malformed.append("search_mode")
    seconds = source.get("auxiliary_seconds")
    if (
        "auxiliary_seconds" in source
        and (not _numeric(seconds) or float(seconds) < 0)
    ):
        malformed.append("auxiliary_seconds")
    approved = source.get("approved_candidate_id")
    if (
        "approved_candidate_id" in source
        and approved is not None
        and (not isinstance(approved, str) or not approved)
    ):
        malformed.append("approved_candidate_id")
    if (
        "limit_observations" in source
        and (
            not isinstance(source["limit_observations"], dict)
            or set(source["limit_observations"])
            != {"structured_response_attempts"}
        )
    ):
        malformed.append("limit_observations")
    elif isinstance(source.get("limit_observations"), dict):
        structured = source["limit_observations"].get(
            "structured_response_attempts"
        )
        details = (
            structured.get("observations")
            if isinstance(structured, dict) else None
        )
        maximum_observed = source.get(
            "structured_max_attempts_observed"
        )
        expected_touch = (
            int(maximum_observed >= 4)
            if type(maximum_observed) is int else None
        )
        if (
            not isinstance(structured, dict)
            or type(structured.get("touched_count")) is not int
            or structured["touched_count"] < 0
            or structured.get("touched_count")
            != expected_touch
            or not isinstance(details, dict)
            or set(details) != {
                "max_attempts",
                "attempts",
                "rejected_attempts",
                "exhaustions",
            }
            or details.get("max_attempts") != 4
            or type(details.get("attempts")) is not int
            or details["attempts"] != maximum_observed
            or type(details.get("rejected_attempts")) is not int
            or not 0 <= details["rejected_attempts"] <= details["attempts"]
            or details.get("exhaustions")
            != source.get("structured_attempt_exhaustions")
        ):
            malformed.append("limit_observations")
    return {
        **{
            name: source.get(name)
            for name in CAVEAT_HARNESS_DIAGNOSTIC_FIELDS
        },
        "source_present": bool(source),
        "missing_fields": missing,
        "malformed_fields": sorted(set(malformed)),
        "complete": bool(source) and not missing and not malformed,
    }


def _empty_row(
    spec: dict,
    reason: str,
    *,
    refillable: bool = False,
) -> dict:
    return {
        **{key: spec[key] for key in (
            "run_id", "block", "spawn_index", "cohort", "repeat",
            "scenario", "condition", "arm", "scaffold", "model_request",
            "primary_region", "summary_relpath",
        )},
        "summary_present": False,
        "outcome": None,
        "chosen": None,
        "chosen_label": None,
        "purchase_role": "no_order",
        "preservation_strict": None,
        "strict_binary": None,
        "steps": 0,
        "decision_steps": 0,
        "tool_actions": None,
        "seconds": 0.0,
        "coverage": {},
        "caveat_harness_diagnostics": {},
        "diagnostics_complete": spec["arm"] == "baseline",
        "context_cap_audit": {},
        "context_cap_audit_complete": False,
        "action_error_audit": {},
        "action_error_audit_complete": False,
        "limit_audit": {},
        "limit_audit_complete": False,
        "launch_provenance": None,
        "recovered_same_model_fallback": None,
        "external_infrastructure_evidence": [],
        "terminal_external_infrastructure_evidence": [],
        "cap_audit": {
            "context_caps": {},
            "context_cap_audit_complete": False,
            "action_error_audit": {},
            "action_error_audit_complete": False,
            "limit_categories": {},
            "limit_audit_complete": False,
            "no_bound": False,
        },
        "no_bound": False,
        "field_errors": [reason],
        "aggregate_eligible": False,
        "refillable": refillable,
        "exclusion_reasons": [reason],
    }


def _run_text(campaign_dir: Path, spec: dict, summary: dict) -> str:
    parts = []
    for key in ("run_log_relpath", "launcher_log_relpath"):
        path = campaign_dir / spec[key]
        if path.is_file():
            parts.append(path.read_text(errors="replace"))
    launch_failure = (
        campaign_dir / "launch_failures" / f"{spec['run_id']}.json"
    )
    if launch_failure.is_file():
        parts.append(launch_failure.read_text(errors="replace"))
    parts.append("SUMMARY_ERROR: " + str(summary.get("error") or ""))
    return "\n".join(parts)


def _validated_context_cap_audit(
    trajectory_stats: dict,
    frozen_context_limits: dict,
) -> tuple[dict[str, dict], list[str]]:
    """Validate the common per-run audit emitted by both browser-use arms."""
    errors = []
    audit = trajectory_stats.get("context_cap_audit")
    expected = set(CONTEXT_CAP_NAMES)
    if set(frozen_context_limits) != expected:
        return {}, [
            "frozen context-cap inventory is not the exact eight-cap contract"
        ]
    if not isinstance(audit, dict):
        return {}, ["trajectory lacks common context-cap instrumentation"]
    if audit.get("schema_version") != 1:
        errors.append("context-cap audit schema_version is not 1")
    if audit.get("complete") is not True:
        errors.append(
            "context-cap audit is incomplete"
            + (
                f": {audit.get('error')}"
                if audit.get("error") else ""
            )
        )
    history_items = audit.get("history_items")
    if (
        type(history_items) is not int
        or history_items < 0
    ):
        errors.append("context-cap audit history_items is malformed")
    history_state = audit.get("history_state")
    measurement_basis = audit.get("measurement_basis")
    pre_agent_record = (
        history_state is not None or measurement_basis is not None
    )
    if pre_agent_record and (
        history_state != "not_created"
        or measurement_basis != "failure_before_agent_construction"
    ):
        errors.append(
            "context-cap pre-Agent measurement basis is malformed"
        )
    if pre_agent_record and history_items != 0:
        errors.append(
            "context-cap pre-Agent audit has nonzero history_items"
        )
    limits = audit.get("limits")
    if not isinstance(limits, dict) or set(limits) != expected:
        errors.append("context-cap audit limit inventory is not exact")
        return {}, errors
    normalized = {}
    for name in CONTEXT_CAP_NAMES:
        record = limits[name]
        if not isinstance(record, dict):
            errors.append(f"context-cap audit {name} record is malformed")
            continue
        configured = record.get("configured")
        touched_count = record.get("touched_count")
        max_observed = record.get("max_observed")
        if configured != frozen_context_limits[name]:
            errors.append(
                f"context-cap audit {name} configured value drifted"
            )
        if type(touched_count) is not int or touched_count < 0:
            errors.append(
                f"context-cap audit {name} touched_count is malformed"
            )
        if type(max_observed) is not int or max_observed < 0:
            errors.append(
                f"context-cap audit {name} max_observed is malformed"
            )
        lower_bound = record.get("max_observed_lower_bound")
        if (
            lower_bound is not None
            and (
                type(lower_bound) is not int
                or lower_bound < 0
            )
        ):
            errors.append(
                f"context-cap audit {name} lower bound is malformed"
            )
        normalized[name] = dict(record)
    if pre_agent_record and any(
        record.get("touched_count") != 0
        or record.get("max_observed") != 0
        for record in normalized.values()
    ):
        errors.append(
            "context-cap pre-Agent audit contains nonzero observations"
        )
    return normalized, errors


def _validated_evaluate_result_store(
    trajectory_stats: dict,
    frozen_limit_contract: dict,
    limit_categories: dict,
) -> tuple[dict, list[str]]:
    """Validate common spill counters and bind them to the limit audit."""

    errors = []
    store = trajectory_stats.get("evaluate_result_store")
    expected_fields = {
        "schema_version",
        "inline_chars",
        "single_max_chars",
        "max_serialized_chars",
        "single_bound_touched_count",
        "integrity_failure_count",
        "max_bytes",
        "bytes",
        "byte_bound_touched_count",
        "max_responses",
        "responses",
        "response_bound_touched_count",
        "records",
    }
    if not isinstance(store, dict) or set(store) != expected_fields:
        return {}, [
            "trajectory lacks an exact common evaluate-result store audit"
        ]
    if store.get("schema_version") != 1:
        errors.append("evaluate-result store schema_version is not 1")
    numeric_fields = expected_fields - {"schema_version"}
    malformed = [
        name for name in numeric_fields
        if type(store.get(name)) is not int or store[name] < 0
    ]
    if malformed:
        errors.append(
            "evaluate-result store counters are malformed: "
            + ", ".join(sorted(malformed))
        )
        return dict(store), errors

    frozen_categories = frozen_limit_contract.get("categories")
    if not isinstance(frozen_categories, dict):
        frozen_categories = {}
    safety = frozen_categories.get("safety_backstops")
    if not isinstance(safety, dict):
        safety = {}
    expected_single = (
        safety.get("evaluate_result_single_chars", {})
        .get("configured")
    )
    expected_bytes = (
        safety.get("evaluate_result_store_bytes", {})
        .get("configured")
    )
    expected_responses = (
        safety.get("evaluate_result_store_responses", {})
        .get("configured")
    )
    if (
        store["inline_chars"] != 9999
        or store["single_max_chars"] != expected_single
        or store["max_bytes"] != expected_bytes
        or store["max_responses"] != expected_responses
    ):
        errors.append(
            "evaluate-result store configuration differs from the freeze"
        )
    if (
        store["bytes"] > store["max_bytes"]
        or store["responses"] > store["max_responses"]
        or store["records"] > store["responses"]
        or (
            store["responses"] == 0
            and (store["records"] != 0 or store["bytes"] != 0)
        )
        or (
            store["responses"] > 0
            and (
                store["records"] == 0
                or store["max_serialized_chars"]
                <= store["inline_chars"]
                or store["bytes"]
                < store["records"] * (store["inline_chars"] + 1)
            )
        )
        or (
            store["max_serialized_chars"] > store["single_max_chars"]
            and store["single_bound_touched_count"] == 0
        )
    ):
        errors.append("evaluate-result store usage is internally inconsistent")
    if store["integrity_failure_count"] != 0:
        errors.append("evaluate-result store recorded an integrity failure")

    observed_safety = limit_categories.get("safety_backstops", {})
    observed_fixed = limit_categories.get("fixed_architecture", {})
    expected_audit = {
        "evaluate_result_single_chars": {
            "record": observed_safety.get(
                "evaluate_result_single_chars", {}
            ),
            "touched": store["single_bound_touched_count"],
            "observations": {
                "max_serialized_chars": store["max_serialized_chars"],
                "maximum": store["single_max_chars"],
            },
        },
        "evaluate_result_store_bytes": {
            "record": observed_safety.get(
                "evaluate_result_store_bytes", {}
            ),
            "touched": max(
                store["byte_bound_touched_count"],
                int(store["bytes"] >= store["max_bytes"]),
            ),
            "observations": {
                "used": store["bytes"],
                "maximum": store["max_bytes"],
                "utilization": store["bytes"] / store["max_bytes"],
                "records": store["records"],
            },
        },
        "evaluate_result_store_responses": {
            "record": observed_safety.get(
                "evaluate_result_store_responses", {}
            ),
            "touched": max(
                store["response_bound_touched_count"],
                int(store["responses"] >= store["max_responses"]),
            ),
            "observations": {
                "used": store["responses"],
                "maximum": store["max_responses"],
                "utilization":
                    store["responses"] / store["max_responses"],
                "records": store["records"],
            },
        },
        "evaluate_result_spill": {
            "record": observed_fixed.get("evaluate_result_spill", {}),
            "touched": store["responses"],
            "observations": {
                "spilled_responses": store["responses"],
                "unique_records": store["records"],
                "stored_bytes": store["bytes"],
                "max_serialized_chars": store[
                    "max_serialized_chars"
                ],
            },
        },
    }
    for name, expected in expected_audit.items():
        record = expected["record"]
        if record.get("touched_count") != expected["touched"]:
            errors.append(
                f"evaluate-result store and limit audit disagree: {name}"
            )
            continue
        observations = record.get("observations")
        if not isinstance(observations, dict) or any(
            observations.get(key) != value
            for key, value in expected["observations"].items()
        ):
            errors.append(
                "evaluate-result store observations and limit audit "
                f"disagree: {name}"
            )
    return dict(store), errors


_EXTERNAL_EXCEPTION_RE = re.compile(
    r"\b(?:AuthenticationError|PermissionDeniedError|APIConnectionError|"
    r"APITimeoutError|InternalServerError|RateLimitError|"
    r"ServiceUnavailableError|BadGatewayError|"
    r"httpx\.(?:Read|Connect|Write|Pool)Timeout|"
    r"Connection(?:Refused|Reset)Error|TargetClosedError)\b",
    flags=re.I,
)
_EXTERNAL_HTTP_RE = re.compile(
    r"(?:Error code:|status(?:_code)?\s*[:=]|HTTP[/ ](?:1\.[01]|2)?\s*)"
    r".{0,100}\b(?:401|403|404|408|409|429|500|502|503|504)\b",
    flags=re.I,
)
_EXTERNAL_TEXT_MARKERS = (
    "CAVEAT_EVALUATOR_GET_RETRIES_EXHAUSTED",
    "CAVEAT_LAUNCHER_LOG_CREATE_FAILED",
    "CAVEAT_RUN_PROCESS_SPAWN_FAILED",
    "DEPLOYMENT_NOT_FOUND",
    "TRAPI: Unauthorized",
    "TRAPI: Rate Limit Exceeded",
    "Page.navigate() timed out",
    "reconnection attempts failed",
    "Target page, context or browser has been closed",
    "browser crashed",
)


def _external_infrastructure_evidence(text: str) -> list[str]:
    evidence = []
    exception = _EXTERNAL_EXCEPTION_RE.search(text)
    if exception:
        evidence.append(exception.group(0))
    status = _EXTERNAL_HTTP_RE.search(text)
    if status:
        evidence.append(re.sub(r"\s+", " ", status.group(0))[:160])
    evidence.extend(
        marker for marker in _EXTERNAL_TEXT_MARKERS if marker in text
    )
    return list(dict.fromkeys(evidence))


def _row(
    campaign_dir: Path,
    spec: dict,
    catalog: dict,
    caps: dict,
    limit_near_fraction: float,
    manifest: dict | None = None,
) -> dict:
    summary_path = campaign_dir / spec["summary_relpath"]
    trajectory_path = campaign_dir / spec["trajectory_relpath"]
    if not summary_path.is_file():
        external = _external_infrastructure_evidence(
            _run_text(campaign_dir, spec, {})
        )
        row = _empty_row(
            spec,
            "expected summary is missing",
            refillable=bool(external),
        )
        row["external_infrastructure_evidence"] = external
        row["terminal_external_infrastructure_evidence"] = external
        if external:
            row["exclusion_reasons"].append(
                "pre-agent external infrastructure failure"
            )
        return row
    try:
        summary = _read_json(summary_path)
    except Exception as exc:  # noqa: BLE001
        return _empty_row(spec, f"summary is unreadable: {exc}")
    try:
        trajectory = _read_json(trajectory_path)
    except Exception as exc:  # noqa: BLE001
        return _empty_row(spec, f"trajectory is unreadable: {exc}")
    field_errors = []
    provenance = None
    if manifest is not None:
        try:
            provenance = verify_launch_receipt(
                campaign_dir, manifest, spec
            )
        except ValueError as exc:
            field_errors.append(f"launch provenance invalid: {exc}")
    expected = {
        "env": "amazon",
        "scaffold": spec["scaffold"],
        "model": spec["model_recorded"],
        "task_id": f"{spec['scenario']}-{spec['variant']}",
        "condition": spec["condition"],
    }
    for key, wanted in expected.items():
        if summary.get(key) != wanted:
            field_errors.append(
                f"{key}={summary.get(key)!r}, expected {wanted!r}"
            )
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
    chosen = summary.get("chosen")
    role = _purchase_role(chosen, catalog)
    outcome = summary.get("outcome")
    behavioral_no_order = outcome in {"none", "error", "skipped", None}
    pstar = summary.get("preservation_strict")
    binary = summary.get("strict_binary")
    if behavioral_no_order and pstar is None:
        pstar = 0.0
    if behavioral_no_order and binary is None:
        binary = 0.0
    if not _numeric(pstar) or not 0 <= float(pstar) <= 1:
        field_errors.append("preservation_strict is missing or outside [0,1]")
        pstar = None
    else:
        pstar = float(pstar)
    if not _numeric(binary) or float(binary) not in {0.0, 1.0}:
        field_errors.append("strict_binary is missing or not 0/1")
        binary = None
    else:
        binary = float(binary)
    if chosen and chosen not in catalog["asins"] and pstar not in (0, 0.0):
        field_errors.append("off-catalog choice has nonzero P*")

    text = _run_text(campaign_dir, spec, summary)
    all_confounds = _scan(text, CONFOUND_PATTERNS)
    fallback_switch = all_confounds.get("fallback_switch")
    # A switch is non-confounding only where this run's frozen route explicitly
    # contains another endpoint for the same logical model.  A one-route weak model
    # freeze still treats an unexpected fallback marker as a confound.
    recovered_fallback = (
        fallback_switch if len(spec.get("region_order") or []) >= 2 else None
    )
    confounds = {
        key: value for key, value in all_confounds.items()
        if key != "fallback_switch" or recovered_fallback is None
    }
    harness = _scan(text, HARNESS_PATTERNS)
    events = _scan_event_timeouts(text, caps["event_timeouts_seconds"])
    step_fraction = steps / caps["max_steps"]
    time_fraction = seconds / caps["cell_timeout_seconds"]
    step_bound = steps >= caps["max_steps"]
    time_bound = seconds >= caps["cell_timeout_seconds"] * 0.99
    diagnostics = (
        _caveat_harness_diagnostics(trajectory)
        if spec["arm"] == "caveat_harness"
        else {}
    )
    trajectory_stats = trajectory.get("stats") or {}
    frozen_context_limits = (
        manifest["runtime_dependencies"]["agent_behavior_limits"][
            "context_limits"
        ]
        if manifest is not None else {
            "action_error_chars": 20000,
            "action_results_chars": 60000,
            "evaluate_memory_chars": 10000,
            "extract_already_collected_items": 100,
            "extract_memory_chars": 10000,
            "extract_page_chunk_chars": 100000,
            "max_clickable_elements_chars": 40000,
            "read_state_chars": 60000,
        }
    )
    context_cap_audit, context_cap_errors = (
        _validated_context_cap_audit(
            trajectory_stats, frozen_context_limits
        )
    )
    field_errors.extend(context_cap_errors)
    context_cap_audit_complete = not context_cap_errors
    frozen_limit_contract = (
        manifest.get("limit_contract")
        if manifest is not None else runtime_limit_contract(
            near_fraction=float(limit_near_fraction),
        )
    )
    lossy_context_names = set(
        (frozen_limit_contract.get("categories") or {}).get(
            "lossy_context_limits", {}
        )
    )
    fixed_action_error_declared = (
        (frozen_limit_contract.get("categories") or {}).get(
            "fixed_architecture", {}
        ).get("agent_output_validation_feedback_rendering")
        is not None
    )
    context_cap_touched = any(
        name in lossy_context_names
        and not (
            name == "action_error_chars"
            and fixed_action_error_declared
        )
        and record.get("touched_count", 0) > 0
        for name, record in context_cap_audit.items()
    )
    near_policy = (
        frozen_limit_contract.get("near_policy")
        if isinstance(frozen_limit_contract, dict)
        else None
    )
    named_near_fractions = {}
    for name in (
        "evaluate_result_single_fraction",
        "evaluate_result_store_fraction",
    ):
        value = (
            near_policy.get(name)
            if isinstance(near_policy, dict) else None
        )
        if (
            not _numeric(value)
            or not 0 < float(value) <= 1
        ):
            field_errors.append(
                f"frozen limit contract has malformed near policy: {name}"
            )
            named_near_fractions[name] = float(limit_near_fraction)
        else:
            named_near_fractions[name] = float(value)
            if float(value) != float(limit_near_fraction):
                field_errors.append(
                    f"frozen {name} differs from campaign near fraction"
                )
    raw_limit_audit = trajectory_stats.get("limit_audit")
    if not isinstance(frozen_limit_contract, dict):
        limit_audit_errors = ["frozen limit contract is absent"]
        limit_audit = {}
    else:
        limit_audit_errors = validate_limit_audit(
            raw_limit_audit,
            frozen_limit_contract,
            spec["arm"],
        )
        limit_audit = (
            raw_limit_audit if isinstance(raw_limit_audit, dict) else {}
        )
    field_errors.extend(limit_audit_errors)
    limit_audit_complete = not limit_audit_errors
    raw_limit_categories = limit_audit.get("categories")
    limit_categories = (
        raw_limit_categories
        if isinstance(raw_limit_categories, dict)
        else {}
    )
    action_error_audit, action_error_errors = (
        validate_action_error_partition(
            trajectory_stats,
            trajectory_stats.get("context_cap_audit"),
            limit_categories,
            frozen_limit_contract,
        )
    )
    field_errors.extend(action_error_errors)
    action_error_audit_complete = not action_error_errors
    fixed_extract_declared = (
        (frozen_limit_contract.get("categories") or {}).get(
            "fixed_architecture", {}
        ).get("extract_result_file_externalization")
        if isinstance(frozen_limit_contract, dict) else None
    )
    if fixed_extract_declared is not None:
        raw_extract = context_cap_audit.get("extract_memory_chars")
        fixed_extract = limit_categories.get(
            "fixed_architecture", {}
        ).get("extract_result_file_externalization")
        if (
            not isinstance(raw_extract, dict)
            or not isinstance(fixed_extract, dict)
            or fixed_extract.get("touched_count")
            != raw_extract.get("touched_count")
            or fixed_extract.get("observations", {}).get(
                "externalized_results"
            ) != raw_extract.get("touched_count")
            or fixed_extract.get("observations", {}).get(
                "max_result_chars"
            ) != raw_extract.get("max_observed")
            or fixed_extract.get("observations", {}).get(
                "threshold_chars"
            ) != raw_extract.get("configured")
        ):
            field_errors.append(
                "extract externalization audit is not cross-bound"
            )
    if isinstance(frozen_limit_contract, dict):
        evaluate_store, evaluate_store_errors = (
            _validated_evaluate_result_store(
                trajectory_stats,
                frozen_limit_contract,
                limit_categories,
            )
        )
    else:
        evaluate_store = {}
        evaluate_store_errors = [
            "cannot validate evaluate-result store without limit contract"
        ]
    field_errors.extend(evaluate_store_errors)
    evaluate_store_complete = not evaluate_store_errors
    evaluate_store_byte_fraction = (
        evaluate_store["bytes"] / evaluate_store["max_bytes"]
        if evaluate_store_complete
        and evaluate_store["max_bytes"] > 0
        else None
    )
    evaluate_single_fraction = (
        evaluate_store["max_serialized_chars"]
        / evaluate_store["single_max_chars"]
        if evaluate_store_complete
        and evaluate_store["single_max_chars"] > 0
        else None
    )
    evaluate_store_response_fraction = (
        evaluate_store["responses"]
        / evaluate_store["max_responses"]
        if evaluate_store_complete
        and evaluate_store["max_responses"] > 0
        else None
    )
    evaluate_store_near = any((
        (
            evaluate_single_fraction is not None
            and evaluate_single_fraction
            >= named_near_fractions[
                "evaluate_result_single_fraction"
            ]
        ),
        (
            evaluate_store_byte_fraction is not None
            and evaluate_store_byte_fraction
            >= named_near_fractions[
                "evaluate_result_store_fraction"
            ]
        ),
        (
            evaluate_store_response_fraction is not None
            and evaluate_store_response_fraction
            >= named_near_fractions[
                "evaluate_result_store_fraction"
            ]
        ),
    ))
    declared_safety_touched = any(
        record.get("touched_count", 0) > 0
        for category in ("safety_backstops", "lossy_context_limits")
        for record in (limit_categories.get(category) or {}).values()
        if isinstance(record, dict)
    )
    decision_steps = trajectory_stats.get("decision_steps", steps)
    tool_actions = trajectory_stats.get("tool_actions")
    diagnostics_complete = (
        True if spec["arm"] == "baseline"
        else diagnostics.get("complete") is True
    )
    if spec["arm"] == "caveat_harness" and not diagnostics_complete:
        field_errors.append(
            "CAVEAT-Harness diagnostics are missing or malformed"
        )
    backstop_touched = (
        step_bound or time_bound or bool(harness) or bool(events)
        or context_cap_touched
        or declared_safety_touched
    )
    confound_touched = bool(confounds)
    run_dir = campaign_dir / spec["browser_run_relpath"]
    infra = classify_run(str(run_dir))
    external_evidence = _external_infrastructure_evidence(text)
    summary_external_evidence = _external_infrastructure_evidence(
        str(summary.get("error") or "")
    )
    terminal_infra = (
        outcome in {"none", "error", "skipped", None}
        and (
            bool(summary_external_evidence)
            or (
                outcome == "none"
                and infra.get("class") == INFRA
                and bool(external_evidence)
            )
        )
    )
    exclusion_reasons = list(field_errors)
    if confound_touched:
        exclusion_reasons.append(
            "fallback/retry exhaustion/model-output truncation touched"
        )
    if backstop_touched:
        exclusion_reasons.append("a harness or evidence backstop touched")
    if terminal_infra:
        exclusion_reasons.append("terminal external infrastructure failure")
    # Missing CAVEAT-Harness instrumentation invalidates a scientific claim but
    # is an implementation error, not permission to cherry-pick a replacement.
    aggregate_eligible = not exclusion_reasons
    # Confirmatory attempts are replaceable only when external infrastructure
    # prevented a measurement.  Model failures, scaffold bugs, malformed output,
    # and a bound safety ceiling remain preserved invalid outcomes; they are not
    # silently converted into another draw.
    refillable = terminal_infra
    no_bound = (
        not backstop_touched
        and not confound_touched
        and not evaluate_store_near
        and context_cap_audit_complete
        and action_error_audit_complete
        and limit_audit_complete
        and evaluate_store_complete
    )
    coverage = {
        key: diagnostics.get(key)
        for key in (
            "checkpoint_candidate_count",
            "frontier_inspected_count",
            "frontier_advertised_count",
            "frontier_coverage_mode",
            "frontier_advertised_page_count",
            "frontier_enumerated_page_count",
            "approved_candidate_id",
        )
    } if diagnostics else {}
    return {
        **{key: spec[key] for key in (
            "run_id", "block", "spawn_index", "cohort", "repeat",
            "scenario", "condition", "arm", "scaffold", "model_request",
            "primary_region", "summary_relpath",
        )},
        "summary_present": True,
        "outcome": outcome,
        "chosen": chosen,
        "chosen_label": summary.get("chosen_label"),
        "purchase_role": role,
        "preservation_strict": pstar,
        "strict_binary": binary,
        "steps": steps,
        "decision_steps": decision_steps,
        "tool_actions": tool_actions,
        "seconds": seconds,
        "coverage": coverage,
        "caveat_harness_diagnostics": diagnostics,
        "diagnostics_complete": diagnostics_complete,
        "context_cap_audit": context_cap_audit,
        "context_cap_audit_complete": context_cap_audit_complete,
        "action_error_audit": action_error_audit,
        "action_error_audit_complete": action_error_audit_complete,
        "evaluate_result_store": evaluate_store,
        "evaluate_result_store_complete": evaluate_store_complete,
        "limit_audit": limit_audit,
        "limit_audit_complete": limit_audit_complete,
        "launch_provenance": (
            {
                "receipt_sha256": provenance["sha256"],
                "checkpoint_sha256": provenance["checkpoint_sha256"],
                "refill_coexistence_snapshot_sha256": provenance[
                    "refill_coexistence"
                ]["snapshot_sha256"],
                "refill_jobs_sum": provenance[
                    "refill_coexistence"
                ]["refill_jobs_sum"],
                "projected_browser_max": provenance[
                    "refill_coexistence"
                ]["projected_browser_max"],
                "refill_port_ranges": provenance[
                    "refill_coexistence"
                ]["refill_port_ranges"],
            }
            if provenance else None
        ),
        "recovered_same_model_fallback": recovered_fallback,
        "cap_audit": {
            "max_steps": caps["max_steps"],
            "step_utilization": round(step_fraction, 8),
            "cell_timeout_seconds": caps["cell_timeout_seconds"],
            "time_utilization": round(time_fraction, 8),
            "evaluate_store_byte_utilization": (
                round(evaluate_store_byte_fraction, 8)
                if evaluate_store_byte_fraction is not None else None
            ),
            "evaluate_single_utilization": (
                round(evaluate_single_fraction, 8)
                if evaluate_single_fraction is not None else None
            ),
            "evaluate_store_response_utilization": (
                round(evaluate_store_response_fraction, 8)
                if evaluate_store_response_fraction is not None else None
            ),
            "evaluate_store_near_25_percent": evaluate_store_near,
            "evaluate_single_near_fraction":
                named_near_fractions[
                    "evaluate_result_single_fraction"
                ],
            "evaluate_store_near_fraction":
                named_near_fractions[
                    "evaluate_result_store_fraction"
                ],
            "confound_signatures": confounds,
            "recovered_same_model_fallback": recovered_fallback,
            "harness_signatures": harness,
            "event_timeout_signatures": events,
            "context_caps": context_cap_audit,
            "context_cap_audit_complete": context_cap_audit_complete,
            "action_error_audit": action_error_audit,
            "action_error_audit_complete": action_error_audit_complete,
            "limit_categories": limit_categories,
            "limit_audit_complete": limit_audit_complete,
            "no_bound": no_bound,
        },
        "no_bound": no_bound,
        "field_errors": field_errors,
        "infra_classification": infra,
        "external_infrastructure_evidence": external_evidence,
        "terminal_external_infrastructure_evidence": (
            summary_external_evidence or external_evidence
            if terminal_infra else []
        ),
        "aggregate_eligible": aggregate_eligible,
        "refillable": refillable,
        "exclusion_reasons": exclusion_reasons,
    }


def _quantile(values: list[float], fraction: float) -> float:
    values = sorted(values)
    if not values:
        return float("nan")
    index = (len(values) - 1) * fraction
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return values[lower]
    return values[lower] + (
        values[upper] - values[lower]
    ) * (index - lower)


def _scenario_cluster_bootstrap(
    scenario_mean_deltas: dict[str, float],
    *,
    samples: int = 20_000,
    seed: int = 20260728,
) -> list[float]:
    """Resample independent scenario-cluster mean deltas."""
    scenarios = sorted(scenario_mean_deltas)
    if not scenarios:
        return []
    rng = random.Random(seed)
    draws = []
    for _ in range(samples):
        selected = [rng.choice(scenarios) for _ in scenarios]
        draws.append(statistics.mean(
            scenario_mean_deltas[scenario]
            for scenario in selected
        ))
    return draws


def _exact_scenario_cluster_sign_randomization_p(
    scenario_mean_deltas: dict[str, float],
) -> float:
    """One-sided exact sign test over independent scenario clusters.

    Repeated run-pair deltas must already be averaged within scenario.  Taking
    a mapping makes the independence unit explicit and prevents the reporter
    from silently treating repeated runs as independent sign assignments.
    """
    scenarios = sorted(scenario_mean_deltas)
    if not scenarios:
        return float("nan")
    deltas = [scenario_mean_deltas[scenario] for scenario in scenarios]
    observed = statistics.mean(deltas)
    hits = 0
    total = 0
    for signs in itertools.product((-1.0, 1.0), repeat=len(deltas)):
        value = statistics.mean(
            sign * delta for sign, delta in zip(signs, deltas)
        )
        if value >= observed - 1e-12:
            hits += 1
        total += 1
    return hits / total


def _cohort_result(rows: list[dict], cohort: str) -> dict:
    selected = [row for row in rows if row["cohort"] == cohort]
    by_key = defaultdict(dict)
    for row in selected:
        key = (row["scenario"], row["repeat"])
        by_key[key][row["arm"]] = row
    malformed = {
        f"{scenario}/r{repeat}": sorted(arms)
        for (scenario, repeat), arms in by_key.items()
        if set(arms) != {"baseline", "caveat_harness"}
    }
    if malformed:
        raise ValueError(f"{cohort}: malformed A/B pairs: {malformed}")
    pairs = []
    for (scenario, repeat), arms in sorted(by_key.items()):
        baseline = arms["baseline"]
        enhanced = arms["caveat_harness"]
        pairs.append({
            "scenario": scenario,
            "repeat": repeat,
            "baseline_run_id": baseline["run_id"],
            "caveat_harness_run_id": enhanced["run_id"],
            "baseline_pstar": baseline["preservation_strict"],
            "caveat_harness_pstar": enhanced["preservation_strict"],
            "delta_pstar": (
                enhanced["preservation_strict"]
                - baseline["preservation_strict"]
            ),
            "baseline_binary": baseline["strict_binary"],
            "caveat_harness_binary": enhanced["strict_binary"],
            "delta_binary": (
                enhanced["strict_binary"] - baseline["strict_binary"]
            ),
        })
    paired_run_deltas = [pair["delta_pstar"] for pair in pairs]
    scenario_cluster_deltas = {
        scenario: statistics.mean(
            pair["delta_pstar"] for pair in pairs
            if pair["scenario"] == scenario
        )
        for scenario in sorted({pair["scenario"] for pair in pairs})
    }
    bootstrap = _scenario_cluster_bootstrap(scenario_cluster_deltas)
    n_scenario_clusters = len(scenario_cluster_deltas)
    randomization_assignments = 2 ** n_scenario_clusters
    baseline_rows = [row for row in selected if row["arm"] == "baseline"]
    caveat_harness_rows = [
        row for row in selected if row["arm"] == "caveat_harness"
    ]
    return {
        "cohort": cohort,
        "n_run_pairs": len(pairs),
        "n_scenario_clusters": n_scenario_clusters,
        "inference_unit": "scenario_cluster",
        "baseline_mean_pstar": statistics.mean(
            row["preservation_strict"] for row in baseline_rows
        ),
        "caveat_harness_mean_pstar": statistics.mean(
            row["preservation_strict"] for row in caveat_harness_rows
        ),
        "paired_run_mean_delta_pstar": statistics.mean(paired_run_deltas),
        "mean_scenario_cluster_delta_pstar": statistics.mean(
            scenario_cluster_deltas.values()
        ),
        "scenario_cluster_bootstrap_95_ci": [
            _quantile(bootstrap, 0.025),
            _quantile(bootstrap, 0.975),
        ],
        "one_sided_exact_scenario_cluster_sign_randomization_p": (
            _exact_scenario_cluster_sign_randomization_p(
                scenario_cluster_deltas
            )
        ),
        "scenario_cluster_randomization_assignments": (
            randomization_assignments
        ),
        "scenario_cluster_randomization_p_resolution": (
            1.0 / randomization_assignments
        ),
        "baseline_strict_successes": int(sum(
            row["strict_binary"] for row in baseline_rows
        )),
        "caveat_harness_strict_successes": int(sum(
            row["strict_binary"] for row in caveat_harness_rows
        )),
        "additional_strict_successes": int(sum(
            pair["delta_binary"] for pair in pairs
        )),
        "scenario_cluster_mean_deltas": scenario_cluster_deltas,
        "scenario_clusters_improved": sum(
            delta > 0 for delta in scenario_cluster_deltas.values()
        ),
        "pairs": pairs,
    }


def _holm_primary(results: dict[str, dict]) -> None:
    primary = ("weak_easy_combined", "sol_high_hard")
    ranked = sorted(
        (
            (
                results[name][
                    "one_sided_exact_scenario_cluster_sign_randomization_p"
                ],
                name,
            )
            for name in primary
        )
    )
    running = 0.0
    total = len(ranked)
    for index, (pvalue, name) in enumerate(ranked):
        adjusted = min(1.0, pvalue * (total - index))
        running = max(running, adjusted)
        results[name]["holm_adjusted_scenario_cluster_p"] = running
    for name in primary:
        low = results[name]["scenario_cluster_bootstrap_95_ci"][0]
        results[name][
            "scenario_cluster_inference_significant_after_holm"
        ] = (
            low > 0
            and results[name]["holm_adjusted_scenario_cluster_p"] < 0.05
        )


def _targets(results: dict[str, dict]) -> dict:
    easy = results["weak_easy_combined"]
    hard = results["sol_high_hard"]
    clean = results["weak_easy_clean"]
    return {
        "weak_easy_combined": {
            "delta_at_least_0.15": (
                easy["mean_scenario_cluster_delta_pstar"] >= 0.15
            ),
            "at_least_four_scenarios_improved": (
                easy["scenario_clusters_improved"] >= 4
            ),
            "at_least_three_additional_strict_successes": (
                easy["additional_strict_successes"] >= 3
            ),
        },
        "sol_high_hard": {
            "delta_at_least_0.15": (
                hard["mean_scenario_cluster_delta_pstar"] >= 0.15
            ),
            "at_least_two_additional_strict_successes": (
                hard["additional_strict_successes"] >= 2
            ),
        },
        "weak_easy_clean": {
            "mean_regression_no_worse_than_0.05": (
                clean["mean_scenario_cluster_delta_pstar"] >= -0.05
            ),
        },
    }


def _ceiling_audit(rows: list[dict], manifest: dict) -> dict:
    caps = manifest["caps"]
    behavior = manifest["runtime_dependencies"]["agent_behavior_limits"]
    context = behavior["context_limits"]
    frozen_limit_contract = manifest.get("limit_contract")
    if not isinstance(frozen_limit_contract, dict):
        frozen_limit_contract = runtime_limit_contract(
            near_fraction=float(manifest["limit_near_fraction"]),
        )
    near_policy = frozen_limit_contract.get("near_policy")
    near_policy_errors = []

    def named_near_fraction(name: str) -> float:
        value = (
            near_policy.get(name)
            if isinstance(near_policy, dict) else None
        )
        if (
            not _numeric(value)
            or not 0 < float(value) <= 1
        ):
            near_policy_errors.append(name)
            return float(manifest["limit_near_fraction"])
        if float(value) != float(manifest["limit_near_fraction"]):
            near_policy_errors.append(name)
        return float(value)

    evaluate_single_near_fraction = named_near_fraction(
        "evaluate_result_single_fraction"
    )
    evaluate_store_near_fraction = named_near_fraction(
        "evaluate_result_store_fraction"
    )

    def ids(predicate) -> list[str]:
        return sorted(row["run_id"] for row in rows if predicate(row))

    def harness(label: str):
        return ids(
            lambda row: label in (
                row.get("cap_audit", {}).get("harness_signatures") or {}
            )
        )

    def confound(label: str):
        return ids(
            lambda row: label in (
                row.get("cap_audit", {}).get("confound_signatures") or {}
            )
        )

    def declared_touched(row: dict, name: str) -> bool:
        return (
            row.get("cap_audit", {})
            .get("limit_categories", {})
            .get("safety_backstops", {})
            .get(name, {})
            .get("touched_count", 0)
            > 0
        )

    core = {
        "max_steps": {
            "configured": caps["max_steps"],
            "touched_runs": ids(
                lambda row: row.get("cap_audit", {}).get(
                    "step_utilization", 0
                ) >= 1.0
            ),
        },
        "cell_timeout_seconds": {
            "configured": caps["cell_timeout_seconds"],
            "touched_runs": sorted(set(
                ids(
                    lambda row: row.get("cap_audit", {}).get(
                        "time_utilization", 0
                    ) >= 0.99
                ) + harness("cell_timeout")
            )),
        },
        "llm_timeout_seconds": {
            "configured": caps["llm_timeout_seconds"],
            "touched_runs": harness("llm_timeout"),
        },
        "llm_http_timeout_seconds": {
            "configured": caps["llm_http_timeout_seconds"],
            "touched_runs": harness("llm_http_timeout"),
        },
        "step_timeout_seconds": {
            "configured": caps["step_timeout_seconds"],
            "touched_runs": harness("step_timeout"),
        },
        "extract_llm_timeout_seconds": {
            "configured": caps["extract_llm_timeout_seconds"],
            "touched_runs": harness("extract_llm_timeout"),
        },
        "cdp_request_timeout_seconds": {
            "configured": caps["cdp_request_timeout_seconds"],
            "touched_runs": harness("cdp_timeout"),
        },
        "browser_action_timeout_seconds": {
            "configured": caps["browser_action_timeout_seconds"],
            "touched_runs": harness("browser_action_timeout"),
        },
        "max_consecutive_failures": {
            "configured": caps["max_consecutive_failures"],
            "touched_runs": harness("max_failures_stop"),
        },
        "max_completion_tokens": {
            "configured": caps["max_completion_tokens"],
            "touched_runs": confound("model_output_truncation"),
        },
        "llm_sdk_max_retries": {
            "configured": caps["llm_sdk_max_retries"],
            "touched_runs": confound("sdk_retry_exhaustion"),
        },
        "fallback_llm_depth": {
            "configured": behavior["fallback_llm_depth"],
            "touched_runs": [],
            "observed_same_model_regional_fallback_runs": ids(
                lambda row: bool(
                    row.get("recovered_same_model_fallback")
                )
            ),
        },
        "max_actions_per_step": {
            "configured": behavior["max_actions_per_step"],
            "touched_runs": [],
        },
        "max_concurrent_browsers": {
            "configured": caps["max_concurrent_browsers"],
            "scheduled_peak": 10,
            "touched_runs": [],
        },
        "spawn_stagger_seconds": {
            "configured": caps["spawn_stagger_seconds"],
            "touched_runs": [],
        },
        "evaluate_result_store_bytes": {
            "configured": frozen_limit_contract["categories"][
                "safety_backstops"
            ]["evaluate_result_store_bytes"]["configured"],
            "near_fraction": evaluate_store_near_fraction,
            "touched_runs": ids(
                lambda row: declared_touched(
                    row, "evaluate_result_store_bytes"
                )
            ),
            "near_runs": ids(
                lambda row: (
                    row.get("cap_audit", {}).get(
                        "evaluate_store_byte_utilization"
                    ) or 0
                ) >= evaluate_store_near_fraction
            ),
        },
        "evaluate_result_single_chars": {
            "configured": frozen_limit_contract["categories"][
                "safety_backstops"
            ]["evaluate_result_single_chars"]["configured"],
            "near_fraction": evaluate_single_near_fraction,
            "touched_runs": ids(
                lambda row: declared_touched(
                    row, "evaluate_result_single_chars"
                )
            ),
            "near_runs": ids(
                lambda row: (
                    row.get("cap_audit", {}).get(
                        "evaluate_single_utilization"
                    ) or 0
                ) >= evaluate_single_near_fraction
            ),
        },
        "evaluate_result_store_responses": {
            "configured": frozen_limit_contract["categories"][
                "safety_backstops"
            ]["evaluate_result_store_responses"]["configured"],
            "near_fraction": evaluate_store_near_fraction,
            "touched_runs": ids(
                lambda row: declared_touched(
                    row, "evaluate_result_store_responses"
                )
            ),
            "near_runs": ids(
                lambda row: (
                    row.get("cap_audit", {}).get(
                        "evaluate_store_response_utilization"
                    ) or 0
                ) >= evaluate_store_near_fraction
            ),
        },
    }
    expected_context_caps = set(CONTEXT_CAP_NAMES)
    lossy_context_names = set(
        frozen_limit_contract["categories"].get(
            "lossy_context_limits", {}
        )
    )
    action_partition_declared = (
        frozen_limit_contract["categories"].get(
            "fixed_architecture", {}
        ).get("agent_output_validation_feedback_rendering")
        is not None
    )
    unmeasured_context_caps = []
    for name in sorted(expected_context_caps | set(context)):
        configured = context.get(name)
        missing_runs = ids(
            lambda row, cap=name: (
                row.get("cap_audit", {}).get(
                    "context_cap_audit_complete"
                ) is not True
                or cap not in (
                    row.get("cap_audit", {}).get("context_caps") or {}
                )
            )
        )
        if name not in context or name not in expected_context_caps:
            unmeasured_context_caps.append(name)
            core[name] = {
                "configured": configured,
                "audit_status": "unmeasured",
                "touched_runs": None,
                "missing_run_audits": missing_runs,
                "reason": (
                    "cap is missing from the frozen inventory"
                    if name not in context else
                    "unexpected cap is not covered by the frozen common audit"
                ),
            }
        elif missing_runs:
            unmeasured_context_caps.append(name)
            core[name] = {
                "configured": configured,
                "audit_status": "unmeasured",
                "touched_runs": None,
                "missing_run_audits": missing_runs,
                "reason": (
                    "one or more runs lack a complete common context-cap audit"
                ),
            }
        else:
            partitioned_action = (
                name == "action_error_chars"
                and action_partition_declared
            )
            core[name] = {
                "configured": configured,
                "audit_status": "measured",
                "source": "trajectory.stats.context_cap_audit",
                "invalidating": (
                    name in lossy_context_names
                    and not partitioned_action
                ),
                "classification": (
                    "provenance_partitioned_fixed_or_lossy"
                    if partitioned_action else
                    "lossy_context_limit"
                    if name in lossy_context_names
                    else "fixed_architecture"
                ),
                "missing_run_audits": [],
                "touched_runs": ids(
                    lambda row, cap=name: (
                        row["cap_audit"]["context_caps"][cap][
                            "touched_count"
                        ] > 0
                    )
                ),
            }
    events = {
        name: {
            "configured_seconds": seconds,
            "touched_runs": ids(
                lambda row, event=name: event in (
                    row.get("cap_audit", {}).get(
                        "event_timeout_signatures"
                    ) or {}
                )
            ),
        }
        for name, seconds in caps["event_timeouts_seconds"].items()
    }
    missing_limit_audit_runs = ids(
        lambda row: row.get("limit_audit_complete") is not True
        or row.get("cap_audit", {}).get(
            "limit_audit_complete"
        ) is not True
    )
    declared_limits = {}
    declared_safety_touched_names = []
    for category, records in frozen_limit_contract["categories"].items():
        declared_limits[category] = {}
        for name, frozen in records.items():
            applicable_rows = [
                row for row in rows
                if row.get("arm") in frozen["applicability"]
            ]
            missing = sorted(
                row["run_id"] for row in applicable_rows
                if row.get("limit_audit_complete") is not True
                or name not in (
                    row.get("cap_audit", {}).get(
                        "limit_categories", {}
                    ).get(category, {})
                )
            )
            touched = sorted(
                row["run_id"] for row in applicable_rows
                if (
                    row.get("cap_audit", {}).get(
                        "limit_categories", {}
                    ).get(category, {}).get(name, {}).get(
                        "touched_count", 0
                    ) > 0
                )
            )
            declared_limits[category][name] = {
                "configured": frozen["configured"],
                "applicability": frozen["applicability"],
                "source": frozen["source"],
                "observation_basis": frozen.get(
                    "observation_basis",
                    "runtime_configuration_counter_or_error_marker",
                ),
                "direct_maximum_observed": frozen.get(
                    "direct_maximum_observed"
                ),
                "audit_status": (
                    (
                        "failure_marker_measured"
                        if frozen.get("direct_maximum_observed") is False
                        else "measured"
                    )
                    if not missing else "unmeasured"
                ),
                "missing_run_audits": missing,
                "touched_runs": touched,
            }
            if (
                category in {
                    "safety_backstops", "lossy_context_limits"
                }
                and touched
            ):
                declared_safety_touched_names.append(
                    f"{category}:{name}"
                )
    core["max_actions_per_step"]["architecture_touch_runs"] = (
        declared_limits.get("fixed_architecture", {}).get(
            "max_actions_per_step", {}
        ).get("touched_runs", [])
    )
    core["fallback_llm_depth"]["architecture_touch_runs"] = (
        declared_limits.get("fixed_architecture", {}).get(
            "fallback_llm_depth", {}
        ).get("touched_runs", [])
    )
    missing_evaluate_store_audits = ids(
        lambda row: row.get("evaluate_result_store_complete") is not True
    )
    missing_action_error_audits = (
        ids(
            lambda row: row.get("action_error_audit_complete")
            is not True
        )
        if action_partition_declared else []
    )
    if action_partition_declared and "action_error_chars" in core:
        core["action_error_chars"].update({
            "fixed_architecture_touch_runs": ids(
                lambda row: (
                    row.get("action_error_audit", {}).get(
                        "agent_output_validation", {}
                    ).get("over_cap_count", 0) > 0
                )
            ),
            "residual_lossy_touch_runs": ids(
                lambda row: (
                    row.get("action_error_audit", {}).get(
                        "other_or_unknown", {}
                    ).get("over_cap_count", 0) > 0
                )
            ),
        })
    touched_names = [
        name for name, record in core.items()
        if (
            record.get("invalidating", True)
            and (record.get("touched_runs") or record.get("near_runs"))
        )
    ] + [
        f"event:{name}" for name, record in events.items()
        if record["touched_runs"]
    ] + declared_safety_touched_names
    by_model = {}
    for model in sorted({row["model_request"] for row in rows}):
        model_rows = [row for row in rows if row["model_request"] == model]
        by_model[model] = {
            "runs": len(model_rows),
            "max_step_utilization": max(
                row.get("cap_audit", {}).get("step_utilization", 0)
                for row in model_rows
            ),
            "max_time_utilization": max(
                row.get("cap_audit", {}).get("time_utilization", 0)
                for row in model_rows
            ),
            "not_clear_runs": sorted(
                row["run_id"] for row in model_rows
                if not row["no_bound"]
            ),
        }
    return {
        "inventory_complete": (
            not unmeasured_context_caps
            and not missing_limit_audit_runs
            and not missing_evaluate_store_audits
            and not missing_action_error_audits
            and not near_policy_errors
        ),
        "context_cap_inventory_exact": (
            set(context) == expected_context_caps
        ),
        "context_cap_names": sorted(context),
        "unmeasured_cap_confounds": (
            unmeasured_context_caps
            + (
                ["common_limit_audit"]
                if missing_limit_audit_runs else []
            )
            + (
                ["common_evaluate_result_store_audit"]
                if missing_evaluate_store_audits else []
            )
            + (
                ["common_action_error_provenance_audit"]
                if missing_action_error_audits else []
            )
            + (
                ["evaluate_result_near_policy"]
                if near_policy_errors else []
            )
        ),
        "core": core,
        "declared_limits": declared_limits,
        "limit_contract_sha256": frozen_limit_contract["sha256"],
        "missing_limit_audit_runs": missing_limit_audit_runs,
        "missing_evaluate_result_store_audits":
            missing_evaluate_store_audits,
        "missing_action_error_audits": missing_action_error_audits,
        "evaluate_result_near_policy_errors":
            sorted(set(near_policy_errors)),
        "event_timeouts": events,
        "by_model": by_model,
        "all_ceilings_untouched_and_below_near_threshold": (
            not touched_names
            and not unmeasured_context_caps
            and not missing_limit_audit_runs
            and not missing_evaluate_store_audits
            and not missing_action_error_audits
            and not near_policy_errors
        ),
        "touched_or_near_names": touched_names,
        "max_step_utilization": max(
            row["cap_audit"].get("step_utilization", 0)
            for row in rows
        ),
        "max_time_utilization": max(
            row["cap_audit"].get("time_utilization", 0)
            for row in rows
        ),
        "max_evaluate_store_byte_utilization": max(
            (
                row["cap_audit"].get(
                    "evaluate_store_byte_utilization"
                )
                for row in rows
                if row["cap_audit"].get(
                    "evaluate_store_byte_utilization"
                ) is not None
            ),
            default=None,
        ),
        "max_evaluate_single_utilization": max(
            (
                row["cap_audit"].get("evaluate_single_utilization")
                for row in rows
                if row["cap_audit"].get(
                    "evaluate_single_utilization"
                ) is not None
            ),
            default=None,
        ),
        "max_evaluate_store_response_utilization": max(
            (
                row["cap_audit"].get(
                    "evaluate_store_response_utilization"
                )
                for row in rows
                if row["cap_audit"].get(
                    "evaluate_store_response_utilization"
                ) is not None
            ),
            default=None,
        ),
    }


def build_report(
    campaign_dir: Path,
    *,
    rescore: bool = True,
) -> dict:
    reject_abandoned(campaign_dir, "confirmatory report/rescore")
    manifest = verify_campaign(campaign_dir, quiet=True)
    if manifest.get("kind") != KIND:
        raise ValueError("wrong campaign kind")
    smoke_gate_error = None
    smoke_gate = None
    try:
        smoke_gate = verify_smoke_gate(campaign_dir, manifest)
    except ValueError as exc:
        smoke_gate_error = str(exc)
    rescore_audit = (
        _rescore(manifest, campaign_dir)
        if rescore else _no_rescore_audit(manifest)
    )
    roles = _catalog_roles(campaign_dir, manifest)
    rows = [
        _row(
            campaign_dir,
            spec,
            roles[spec["scenario"]],
            manifest["caps"],
            manifest["limit_near_fraction"],
            manifest,
        )
        for spec in manifest["schedule"]
    ]
    excluded = {
        row["run_id"]: {
            "reasons": row["exclusion_reasons"],
            "refillable": row["refillable"],
        }
        for row in rows if not row["aggregate_eligible"]
    }
    refill_ids = [
        row["run_id"] for row in rows
        if not row["aggregate_eligible"] and row["refillable"]
    ]
    diagnostics_incomplete = [
        row["run_id"] for row in rows
        if row["arm"] == "caveat_harness"
        and row["summary_present"]
        and not row["diagnostics_complete"]
    ]
    bound_runs = [
        row["run_id"] for row in rows if not row["no_bound"]
    ]
    complete = len(rows) == 60 and all(
        row["summary_present"] for row in rows
    )
    ceiling_audit = _ceiling_audit(rows, manifest)
    fresh_strict_rescore = rescore_audit["fresh_complete"] is True
    aggregation_ready = (
        complete
        and fresh_strict_rescore
        and smoke_gate_error is None
        and not excluded
        and not diagnostics_incomplete
        and not bound_runs
        and ceiling_audit["inventory_complete"]
        and not ceiling_audit["unmeasured_cap_confounds"]
        and ceiling_audit[
            "all_ceilings_untouched_and_below_near_threshold"
        ]
    )
    results = None
    targets = None
    if aggregation_ready:
        results = {
            cohort: _cohort_result(rows, cohort)
            for cohort in (
                "weak_easy_combined",
                "weak_easy_clean",
                "sol_high_hard",
            )
        }
        _holm_primary(results)
        targets = _targets(results)
    return {
        "schema_version": 2,
        "kind": "caveat_harness_ab_report",
        "generated_at_utc": _utcnow(),
        "campaign_id": manifest["campaign_id"],
        "manifest_sha256": _read_json(
            campaign_dir / "campaign_manifest.sha256.json"
        )["sha256"],
        "metric_policy": manifest["metric_policy"],
        "refill_coexistence_policy": manifest[
            "refill_coexistence_policy"
        ],
        "rescored_summary_count": rescore_audit[
            "updated_summary_count"
        ],
        "rescore_audit": rescore_audit,
        "validity": {
            "green": aggregation_ready,
            "complete_denominator": complete,
            "expected_runs": 60,
            "observed_summaries": sum(
                row["summary_present"] for row in rows
            ),
            "aggregation_attempted": aggregation_ready,
            "fresh_strict_rescore": fresh_strict_rescore,
            "smoke_gate_valid": smoke_gate_error is None,
            "smoke_gate_error": smoke_gate_error,
            "smoke_gate_sha256": (
                smoke_gate["sha256"] if smoke_gate else None
            ),
            "excluded_runs": excluded,
            "refillable": bool(refill_ids),
            "refill_run_ids": refill_ids,
            "diagnostics_incomplete_runs": diagnostics_incomplete,
            "bound_or_near_bound_runs": bound_runs,
            "cap_inventory_complete": ceiling_audit[
                "inventory_complete"
            ],
            "unmeasured_cap_confounds": ceiling_audit[
                "unmeasured_cap_confounds"
            ],
            "all_caps_untouched": (
                not bound_runs
                and ceiling_audit[
                    "all_ceilings_untouched_and_below_near_threshold"
                ]
            ),
        },
        "ceiling_audit": ceiling_audit,
        "results": results,
        "predeclared_targets": targets,
        "runs": rows,
    }


def _fmt(value: object, digits: int = 4) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _md_cell(value: object) -> str:
    if value is None:
        return "—"
    return str(value).replace("\\", "\\\\").replace("|", "\\|").replace(
        "\n", " "
    )


def markdown_report(report: dict) -> str:
    validity = report["validity"]
    lines = [
        "# CAVEAT-Harness A/B evaluation",
        "",
        f"Validity: **{'GREEN' if validity['green'] else 'RED'}**. "
        f"Summaries: {validity['observed_summaries']}/"
        f"{validity['expected_runs']}. Headline metric: "
        "`preservation_strict` (P*=G·O); `strict_binary` is secondary.",
        "",
    ]
    if report["results"]:
        lines.extend([
            "## Paired results",
            "",
            "The inferential unit is the **scenario cluster**. Repeated "
            "run-pair deltas are averaged within each scenario before the "
            "cluster bootstrap and exact sign randomization. With five "
            "scenario clusters, the exact test has 2⁵=32 sign assignments, "
            "so its raw p-value resolution is 1/32 (0.03125). Effect sizes, "
            "scenario-cluster confidence intervals, and consistency across "
            "the five clusters are primary; Holm-adjusted p-values are "
            "coarse supplementary evidence.",
            "",
            "| cohort | run pairs | scenario clusters | baseline run-mean P* | "
            "CAVEAT-Harness run-mean P* | mean scenario-cluster ΔP* | "
            "95% scenario-cluster bootstrap CI | strict successes B→D | "
            "Holm-adjusted scenario-cluster p |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ])
        for cohort in (
            "weak_easy_combined",
            "weak_easy_clean",
            "sol_high_hard",
        ):
            result = report["results"][cohort]
            ci = result["scenario_cluster_bootstrap_95_ci"]
            lines.append(
                f"| {cohort} | {result['n_run_pairs']} | "
                f"{result['n_scenario_clusters']} | "
                f"{_fmt(result['baseline_mean_pstar'])} | "
                f"{_fmt(result['caveat_harness_mean_pstar'])} | "
                f"{_fmt(result['mean_scenario_cluster_delta_pstar'])} | "
                f"[{_fmt(ci[0])}, {_fmt(ci[1])}] | "
                f"{result['baseline_strict_successes']}→"
                f"{result['caveat_harness_strict_successes']} | "
                f"{_fmt(result.get(
                    'holm_adjusted_scenario_cluster_p'
                ))} |"
            )
        lines.append("")
    else:
        lines.extend([
            "No headline aggregate was produced because the exact paired "
            "denominator or validity invariants are incomplete.",
            "",
        ])
    lines.extend([
        "## Per-run evidence",
        "",
        "| run | arm | scenario | outcome | chosen ASIN | chosen label | role | "
        "P* | B | steps/actions | seconds | coverage | candidates | "
        "checkpoint calls | checkpoint rejects | checkpoint approves | "
        "cap clear |",
        "|---|---|---|---|---|---|---|---:|---:|---:|---:|---:|---:|"
        "---:|---:|---:|:---:|",
    ])
    for row in report["runs"]:
        coverage = row.get("coverage") or {}
        diagnostics = row.get("caveat_harness_diagnostics") or {}
        lines.append(
            f"| {_md_cell(row['run_id'])} | {_md_cell(row['arm'])} | "
            f"{_md_cell(row['scenario'])} | {_md_cell(row['outcome'])} | "
            f"{_md_cell(row['chosen'])} | {_md_cell(row['chosen_label'])} | "
            f"{_md_cell(row['purchase_role'])} | "
            f"{_fmt(row['preservation_strict'])} | "
            f"{_fmt(row['strict_binary'], 0)} | "
            f"{row['decision_steps']}/{_fmt(row['tool_actions'], 0)} | "
            f"{_fmt(row['seconds'], 1)} | "
            f"{_md_cell(coverage.get('frontier_coverage_mode'))}:"
            f"{_fmt(coverage.get('frontier_inspected_count'), 0)}/"
            f"{_fmt(coverage.get('frontier_advertised_count'), 0)}/"
            f"{_fmt(coverage.get('frontier_enumerated_page_count'), 0)} | "
            f"{_fmt(coverage.get('checkpoint_candidate_count'), 0)} | "
            f"{_fmt(diagnostics.get('decision_checkpoint_calls'), 0)} | "
            f"{_fmt(diagnostics.get('decision_checkpoint_rejections'), 0)} | "
            f"{_fmt(diagnostics.get('decision_checkpoint_approvals'), 0)} | "
            f"{'yes' if row['no_bound'] else 'no'} |"
        )
    lines.extend([
        "",
        "## Validity details",
        "",
        f"- Refillable runs: "
        f"{', '.join(validity['refill_run_ids']) or 'none'}",
        f"- Smoke gate: "
        f"{'valid' if validity['smoke_gate_valid'] else validity['smoke_gate_error']}",
        f"- Missing CAVEAT-Harness diagnostics: "
        f"{', '.join(validity['diagnostics_incomplete_runs']) or 'none'}",
        f"- Bound or near-bound runs: "
        f"{', '.join(validity['bound_or_near_bound_runs']) or 'none'}",
        f"- Fresh strict rescore: "
        f"{'complete' if validity['fresh_strict_rescore'] else 'missing/incomplete'}",
        f"- Unmeasured cap confounds: "
        f"{', '.join(validity['unmeasured_cap_confounds']) or 'none'}",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("campaign_dir", type=Path)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--markdown", type=Path)
    parser.add_argument("--no-rescore", action="store_true")
    args = parser.parse_args()
    campaign_dir = args.campaign_dir.resolve()
    json_path = (args.json or campaign_dir / "report.json").resolve()
    markdown_path = (
        args.markdown or campaign_dir / "report.md"
    ).resolve()
    hash_path = json_path.with_name(f"{json_path.name}.sha256.json")
    paths = (json_path, markdown_path, hash_path)
    if len(set(paths)) != len(paths):
        raise SystemExit("report JSON, Markdown, and hash paths must differ")
    existing = [str(path) for path in paths if path.exists()]
    if existing:
        raise SystemExit(
            "refusing to replace create-only report files: "
            + ", ".join(existing)
        )
    # The report's default path performs an in-place strict rescore of every
    # measured summary.  Establish create-only output availability before that
    # mutation so an accidental rerun cannot rewrite evidence and only then
    # refuse to publish.
    report = build_report(
        campaign_dir, rescore=not args.no_rescore
    )
    _write_new_json(json_path, report)
    _write_new_json(hash_path, {
        "schema_version": 1,
        "kind": "caveat_harness_ab_report_hash",
        "path": json_path.name,
        "sha256": _sha_file(json_path),
        "campaign_id": report["campaign_id"],
        "manifest_sha256": report["manifest_sha256"],
    })
    _write_new_text(markdown_path, markdown_report(report))
    validity = report["validity"]
    print(
        f"REPORT {'GREEN' if validity['green'] else 'RED'}: "
        f"{validity['observed_summaries']}/{validity['expected_runs']} "
        f"summaries; refillable={len(validity['refill_run_ids'])}; "
        f"bound={len(validity['bound_or_near_bound_runs'])}"
    )
    print(f"JSON: {json_path}")
    print(f"Hash: {hash_path}")
    print(f"Markdown: {markdown_path}")
    return 0 if validity["green"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
